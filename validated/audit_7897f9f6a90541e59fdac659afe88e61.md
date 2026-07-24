Looking at the actual code, I need to trace the full path carefully before rendering a verdict.

Based on my thorough code analysis, here is my verdict:

---

### Title
Unauthenticated `send_tx` JSON-RPC Allows Pre-insertion of Bridge Transactions with Wrong `fee_paying_type`, Causing Permanent Liveness Failure — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`, `crates/clementine-tx-sender/src/client.rs`, `crates/clementine-tx-sender/src/db/tx_sender.rs`)

---

### Summary

The tx-sender JSON-RPC server exposes a `send_tx` method with no authentication. An attacker who can reach the endpoint can pre-insert a bridge transaction (e.g., MoveToVault, Reimburse) with `fee_paying_type: NoFunding` before the bridge code does. Because `insert_try_to_send` returns early on the first txid match without ever updating `fee_paying_type`, the legitimate bridge code's subsequent call with `FeePayingType::CPFP` is silently discarded. The tx is then processed forever as `NoFunding`, causing `send_raw_transaction` to be called on a 0-fee CPFP parent transaction, which Bitcoin Core rejects, permanently stalling the bridge transaction.

---

### Finding Description

**Step 1 — Unauthenticated entrypoint.**

`start_jsonrpc_server` registers `send_tx` with no authentication middleware. The bind address is configurable as `127.0.0.1` or `0.0.0.0`; the latter is explicitly supported by the code. [1](#0-0) [2](#0-1) 

**Step 2 — First-writer-wins deduplication.**

`insert_try_to_send` calls `check_if_tx_exists_on_txsender` and, if the txid is already present, **returns immediately** with the existing `try_to_send_id` — it never calls `save_tx` and never updates `fee_paying_type`. [3](#0-2) 

**Step 3 — `ON CONFLICT` is a no-op on `fee_paying_type`.**

Even if `save_tx` were reached concurrently, the conflict clause only touches `txid`, leaving `fee_paying_type` and `rbf_signing_info` unchanged. [4](#0-3) 

**Step 4 — Bridge txids are deterministic and predictable.**

`create_move_to_vault_txhandler` constructs the MoveToVault transaction entirely from the deposit outpoint (visible on-chain), the nofn xonly public key (derived from publicly-known verifier keys), and fixed protocol parameters. The txid (computed from non-witness data only) is therefore computable by any observer before the aggregator processes the deposit. [5](#0-4) [6](#0-5) 

**Step 5 — CPFP transactions have 0 fees; `NoFunding` path broadcasts them as-is.**

MoveToVault and Reimburse carry a 0-sat anchor output and rely on CPFP for fee payment. When processed as `NoFunding`, `send_no_funding_tx` calls `send_raw_transaction` directly. Bitcoin Core rejects the 0-fee transaction. The tx-sender logs the error and retries indefinitely — there is no mechanism to correct `fee_paying_type` after insertion. [7](#0-6) [8](#0-7) 

---

### Impact Explanation

A MoveToVault or Reimburse transaction stuck with `FeePayingType::NoFunding` will never confirm. The deposit UTXO (bridge amount) remains locked in the vault address indefinitely. There is no on-chain or off-chain recovery path within the scoped code — the `fee_paying_type` column is write-once at insertion time and never updated. [9](#0-8) 

---

### Likelihood Explanation

- **Accessibility**: The JSON-RPC server can be bound to `0.0.0.0` (explicitly supported), making it reachable from the network with no credentials. Even at `127.0.0.1`, any co-located process can reach it.
- **Txid predictability**: MoveToVault txid is fully deterministic from on-chain deposit data and public protocol parameters. An attacker monitoring the mempool/chain can compute it before the aggregator processes the deposit.
- **Timing window**: The aggregator processes deposits after a configurable confirmation threshold, giving the attacker a multi-block window to pre-insert.
- **No authentication, no rate limiting, no IP allowlist** in the scoped code. [10](#0-9) 

---

### Recommendation

1. **Add authentication** to the `send_tx` JSON-RPC (e.g., a shared secret token, mTLS, or IP allowlist enforced in code, not just configuration).
2. **Validate `fee_paying_type` on conflict**: if a txid already exists, verify the stored `fee_paying_type` matches the caller's intent and return an error if it does not, rather than silently returning the existing id.
3. **Restrict the bind address** to `127.0.0.1` by default in code (not just in the example script), and require explicit opt-in for `0.0.0.0`.

---

### Proof of Concept

```rust
// 1. Observe deposit outpoint on-chain.
// 2. Compute MoveToVault non-witness bytes (deterministic from deposit + protocol params).
// 3. Construct a Bitcoin Transaction with the same inputs/outputs but empty witness.
//    txid = hash(non-witness bytes) — identical to the legitimate MoveToVault txid.
// 4. Pre-insert via unauthenticated JSON-RPC:
let attacker_id = client
    .insert_try_to_send(None, &spoofed_tx, FeePayingType::NoFunding, None, &[], &[], &[], &[])
    .await?;

// 5. Aggregator later calls insert_try_to_send with FeePayingType::CPFP for the same txid.
//    check_if_tx_exists_on_txsender returns attacker_id → early return, CPFP never stored.

// 6. tx-sender loop dispatches the tx via send_no_funding_tx → send_raw_transaction.
//    Bitcoin Core rejects: "min relay fee not met" (0-fee CPFP parent).
//    Loop retries forever. MoveToVault never confirms. BTC permanently locked.
``` [11](#0-10) [12](#0-11)

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L42-89)
```rust
pub async fn start_jsonrpc_server(
    tx_sender_client: TxSenderClient,
    bind_addr: SocketAddr,
) -> Result<TxSenderJsonRpcServer, BridgeError> {
    let server: Server = ServerBuilder::default()
        .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
        .build(bind_addr)
        .await
        .map_err(|e| BridgeError::Eyre(e.into()))?;

    let local_addr = server
        .local_addr()
        .map_err(|e| BridgeError::Eyre(e.into()))?;

    let mut module = RpcModule::new(tx_sender_client.clone());
    module
        .register_async_method("send_tx", |params, client, _| async move {
            let req: InsertTryToSendParams = params.one().map_err(jsonrpc_err)?;

            let raw_tx = hex::decode(&req.signed_tx_hex).map_err(jsonrpc_err)?;
            let signed_tx: Transaction = consensus::deserialize(&raw_tx).map_err(jsonrpc_err)?;

            let mut dbtx = client.db.begin_transaction().await.map_err(jsonrpc_err)?;

            let try_to_send_id = client
                .insert_try_to_send(
                    &mut dbtx,
                    req.tx_metadata,
                    &signed_tx,
                    req.fee_paying_type,
                    req.rbf_signing_info,
                    &req.cancel_outpoints,
                    &req.cancel_txids,
                    &req.activate_txids,
                    &req.activate_outpoints,
                )
                .await
                .map_err(jsonrpc_err)?;

            client
                .db
                .commit_transaction(dbtx)
                .await
                .map_err(jsonrpc_err)?;

            Ok::<u32, ErrorObjectOwned>(try_to_send_id)
        })
        .map_err(|e| BridgeError::Eyre(e.into()))?;
```

**File:** crates/clementine-tx-sender/src/config.rs (L197-212)
```rust
        #[cfg(feature = "json-rpc")]
        let jsonrpc = {
            let port = env_parse_optional::<u16>("TX_SENDER_JSONRPC_PORT")?;
            port.map(|port| {
                let bind = env_optional("TX_SENDER_JSONRPC_BIND")
                    .unwrap_or_else(|| "127.0.0.1".to_string());
                if bind != "127.0.0.1" && bind != "0.0.0.0" {
                    return Err(BridgeError::EnvVarMalformed(
                        "TX_SENDER_JSONRPC_BIND",
                        "bind must be either 127.0.0.1 or 0.0.0.0".to_string(),
                    ));
                }
                Ok(TxSenderJsonRpcConfig { bind, port })
            })
            .transpose()?
        };
```

**File:** crates/clementine-tx-sender/src/client.rs (L71-80)
```rust
        let txid = signed_tx.compute_txid();

        // do not add duplicate transactions to the txsender
        let tx_exists = self
            .db
            .check_if_tx_exists_on_txsender(Some(dbtx), txid)
            .await?;
        if let Some(try_to_send_id) = tx_exists {
            return Ok(try_to_send_id);
        }
```

**File:** crates/clementine-tx-sender/src/db/tx_sender.rs (L223-240)
```rust
    pub async fn check_if_tx_exists_on_txsender(
        &self,
        tx: Option<TxSenderDbTx<'_>>,
        txid: Txid,
    ) -> Result<Option<u32>, BridgeError> {
        let query = sqlx::query_as::<_, (i32,)>(
            "SELECT id FROM tx_sender_try_to_send_txs WHERE txid = $1 LIMIT 1",
        )
        .bind(TxidDB(txid));

        let result: Option<(i32,)> =
            txsender_execute_query_with_tx!(&self.pool, tx, query, fetch_optional)?;

        Ok(match result {
            Some((id,)) => Some(u32::try_from(id).wrap_err("Failed to convert id to u32")?),
            None => None,
        })
    }
```

**File:** crates/clementine-tx-sender/src/db/tx_sender.rs (L251-274)
```rust
        let query = sqlx::query_scalar(
            r#"
            INSERT INTO tx_sender_try_to_send_txs
            (raw_tx, fee_paying_type, tx_metadata, txid, rbf_signing_info)
            VALUES ($1, $2::fee_paying_type, $3, $4, $5)
            ON CONFLICT (txid)
            DO UPDATE SET txid = EXCLUDED.txid
            RETURNING id
            "#,
        )
        .bind(serialize(raw_tx))
        .bind(fee_paying_type)
        .bind(serde_json::to_string(&tx_metadata).wrap_err("Failed to encode tx_metadata to JSON")?)
        .bind(TxidDB(txid))
        .bind(
            serde_json::to_string(&rbf_signing_info)
                .wrap_err("Failed to encode rbf_signing_info to JSON")?,
        );

        let id: i32 = query.fetch_one(&mut **tx).await?;
        u32::try_from(id)
            .wrap_err("Failed to convert id to u32")
            .map_err(Into::into)
    }
```

**File:** core/src/builder/transaction/mod.rs (L306-343)
```rust
pub fn create_move_to_vault_txhandler(
    deposit_data: &mut DepositData,
    paramset: &'static ProtocolParamset,
) -> Result<TxHandler<Unsigned>, BridgeError> {
    let nofn_xonly_pk = deposit_data.get_nofn_xonly_pk()?;
    let deposit_outpoint = deposit_data.get_deposit_outpoint();
    let nofn_script = Arc::new(CheckSig::new(nofn_xonly_pk));
    let security_council_script = Arc::new(Multisig::from_security_council(
        deposit_data.security_council.clone(),
    ));

    let deposit_scripts = deposit_data.get_deposit_scripts(paramset)?;

    Ok(TxHandlerBuilder::new(TransactionType::MoveToVault)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            SpendableTxIn::from_scripts(
                deposit_outpoint,
                paramset.bridge_amount,
                deposit_scripts,
                None,
                paramset.network,
            ),
            SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_output(UnspentTxOut::from_scripts(
            paramset.bridge_amount,
            vec![nofn_script, security_council_script],
            None,
            paramset.network,
        ))
        // always use 0 sat anchor for move_tx, this will keep the amount in move to vault tx exactly the bridge amount
        .add_output(UnspentTxOut::from_partial(anchor_output(Amount::from_sat(
            0,
        ))))
        .finalize())
```

**File:** core/src/deposit.rs (L77-82)
```rust
    pub fn get_move_txid(
        &mut self,
        paramset: &'static ProtocolParamset,
    ) -> Result<Txid, BridgeError> {
        Ok(*create_move_to_vault_txhandler(self, paramset)?.get_txid())
    }
```

**File:** crates/clementine-tx-sender/src/lib.rs (L424-449)
```rust
            let result = match fee_paying_type {
                // Send nonstandard transactions to testnet4 using the mempool.space accelerator.
                // As mempool uses out of band payment, we don't need to do cpfp or rbf.
                _ if self.network == bitcoin::Network::Testnet4
                    && self.is_bridge_tx_nonstandard(&tx) =>
                {
                    self.send_testnet4_nonstandard_tx(&tx, id).await
                }
                FeePayingType::CPFP => {
                    self.send_cpfp_tx(id, tx, tx_metadata, adjusted_fee_rate, current_tip_height)
                        .await
                }
                FeePayingType::RBF | FeePayingType::RbfWtxidGrind => {
                    self.send_rbf_tx(
                        id,
                        tx,
                        tx_metadata,
                        adjusted_fee_rate,
                        rbf_signing_info,
                        current_tip_height,
                        fee_paying_type == FeePayingType::RbfWtxidGrind,
                    )
                    .await
                }
                FeePayingType::NoFunding => self.send_no_funding_tx(id, tx, tx_metadata).await,
            };
```

**File:** crates/clementine-tx-sender/src/lib.rs (L572-617)
```rust
    pub async fn send_no_funding_tx(
        &self,
        try_to_send_id: u32,
        tx: Transaction,
        tx_metadata: Option<TxMetadata>,
    ) -> Result<()> {
        match self.rpc.send_raw_transaction(&tx).await {
            Ok(sent_txid) => {
                tracing::debug!(
                    try_to_send_id,
                    "Successfully sent no funding tx with txid {}",
                    sent_txid
                );
                let _ = self
                    .db
                    .update_tx_debug_sending_state(try_to_send_id, "no_funding_send_success", true)
                    .await;
            }
            Err(e) => {
                let err_str = e.to_string();
                if rpc_errors::is_rejecting_replacement_error(&err_str) {
                    tracing::debug!(
                        try_to_send_id,
                        "No funding tx rejected (tx already in mempool): {err_str}"
                    );
                    return Ok(());
                } else {
                    tracing::error!(
                        "Failed to send no funding tx with try_to_send_id: {try_to_send_id:?} and metadata: {tx_metadata:?}"
                    );
                    log_error_for_tx!(
                        self.db,
                        try_to_send_id,
                        format!("send_raw_transaction error for no funding tx: {err_str}")
                    );
                }
                let _ = self
                    .db
                    .update_tx_debug_sending_state(try_to_send_id, "no_funding_send_failed", true)
                    .await;
                return Err(SendTxError::Other(eyre::eyre!(e)));
            }
        };

        Ok(())
    }
```

**File:** crates/clementine-tx-sender/migrations/0001_init.up.sql (L14-30)
```sql
CREATE TABLE IF NOT EXISTS tx_sender_try_to_send_txs (
    id SERIAL PRIMARY KEY,
    raw_tx BYTEA NOT NULL,
    tx_metadata TEXT,
    fee_paying_type fee_paying_type NOT NULL,
    effective_fee_rate BIGINT,
    txid BYTEA,
    -- first observed chain height when tx was seen confirmed (used for finality tracking)
    seen_at_height INT,
    -- explicit finality flag: TRUE only when confirmations >= finality_depth from RPC
    is_finalized BOOLEAN NOT NULL DEFAULT FALSE,
    last_bump_block_height INT DEFAULT NULL,
    latest_active_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    rbf_signing_info TEXT,
    UNIQUE (txid)
);
```

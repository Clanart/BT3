Based on my full trace of the code, this is a real vulnerability. Here is the analysis:

---

### Title
Unauthenticated JSON-RPC `send_tx` Endpoint Allows Arbitrary Transaction Broadcast and Fee-Payer Fund Drain When Bound to `0.0.0.0` — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

---

### Summary

The tx-sender JSON-RPC server, when configured with `TX_SENDER_JSONRPC_BIND=0.0.0.0`, exposes the `send_tx` method to any network-reachable client with zero authentication. An attacker can submit arbitrary `InsertTryToSendParams` payloads, queue transactions for broadcast, and cause the tx-sender to drain its Bitcoin Core wallet by creating fee-payer UTXOs on behalf of attacker-controlled transactions.

---

### Finding Description

`start_jsonrpc_server` in `crates/clementine-tx-sender/src/jsonrpc/server.rs` is built with `ServerBuilder::default()` — no token, no mTLS, no IP allowlist, no middleware of any kind: [1](#0-0) 

The `send_tx` handler deserializes the caller-supplied hex transaction and calls `TxSenderClient::insert_try_to_send` directly, passing all caller-controlled fields (`fee_paying_type`, `rbf_signing_info`, `cancel_outpoints`, etc.) without any authorization check: [2](#0-1) 

The config explicitly permits `0.0.0.0` as a valid bind address: [3](#0-2) 

Once queued, the tx-sender loop calls `try_to_send_unconfirmed_txs`, which dispatches to `send_cpfp_tx`, `send_rbf_tx`, or `send_no_funding_tx` based on the attacker-supplied `fee_paying_type`: [4](#0-3) 

For `FeePayingType::CPFP`, if the attacker's transaction has a P2A anchor output, `send_cpfp_tx` calls `create_fee_payer_utxo`, which calls `fund_raw_transaction` and `send_raw_transaction` on the Bitcoin Core wallet to create and broadcast a fee-payer UTXO funded from the operator's wallet: [5](#0-4) 

For `FeePayingType::NoFunding`, `send_no_funding_tx` calls `send_raw_transaction` directly on the submitted transaction with no further validation: [6](#0-5) 

---

### Impact Explanation

**Unauthorized transaction broadcast:** Any network-reachable attacker can queue and cause the tx-sender to broadcast arbitrary Bitcoin transactions. This is a direct authorization bypass — only the bridge operator should be able to submit transactions to the tx-sender queue.

**Fee-payer fund drain:** For CPFP submissions, the tx-sender calls `fund_raw_transaction` on the Bitcoin Core wallet and broadcasts a fee-payer transaction to fund the attacker's CPFP package. The operator's wallet funds are spent as fees for attacker-controlled transactions. An attacker can repeat this indefinitely, draining the operator's fee-payer wallet.

**Clarification on the specific claim:** The attacker cannot spend bridge-controlled UTXOs (kickoff, deposit, etc.) because those require valid Taproot signatures the attacker does not possess. The tx-sender broadcasts the submitted transaction as-is without re-signing. However, the fee-payer wallet drain is a real and material fund loss from tx-sender-managed balances.

---

### Likelihood Explanation

Requires only network access to the tx-sender's JSON-RPC port when `TX_SENDER_JSONRPC_BIND=0.0.0.0`. No credentials, keys, or privileged access needed. The `run.sh` script defaults to `127.0.0.1`, but the code explicitly supports `0.0.0.0` as a production configuration option, and any deployment that sets this (e.g., containerized or cloud environments) is fully exposed. [7](#0-6) 

---

### Recommendation

1. Add authentication to the JSON-RPC server (shared secret token in HTTP header, mTLS, or IP allowlist) before accepting any `send_tx` call.
2. If `0.0.0.0` binding is required for inter-service communication, enforce network-level controls (firewall, VPC isolation) AND application-level authentication.
3. Consider removing `0.0.0.0` as a supported bind address entirely, or gating it behind an explicit opt-in with a documented security warning.

---

### Proof of Concept

1. Start tx-sender with `TX_SENDER_JSONRPC_BIND=0.0.0.0` and `TX_SENDER_JSONRPC_PORT=3030`.
2. From a different host on the same network, craft a Bitcoin transaction with a P2A anchor output (or any valid transaction for `NoFunding`).
3. Submit via HTTP:
```json
POST http://<tx-sender-ip>:3030
{"jsonrpc":"2.0","id":1,"method":"send_tx","params":[{"signed_tx_hex":"<hex>","fee_paying_type":"CPFP","cancel_outpoints":[],"cancel_txids":[],"activate_txids":[],"activate_outpoints":[]}]}
```
4. Observe the tx-sender accepts the request (returns a `try_to_send_id`), creates a fee-payer UTXO from the operator's Bitcoin Core wallet, and broadcasts the attacker's transaction as the CPFP parent.
5. Repeat to drain the fee-payer wallet.

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L46-50)
```rust
    let server: Server = ServerBuilder::default()
        .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
        .build(bind_addr)
        .await
        .map_err(|e| BridgeError::Eyre(e.into()))?;
```

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L58-88)
```rust
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
```

**File:** crates/clementine-tx-sender/src/config.rs (L201-209)
```rust
                let bind = env_optional("TX_SENDER_JSONRPC_BIND")
                    .unwrap_or_else(|| "127.0.0.1".to_string());
                if bind != "127.0.0.1" && bind != "0.0.0.0" {
                    return Err(BridgeError::EnvVarMalformed(
                        "TX_SENDER_JSONRPC_BIND",
                        "bind must be either 127.0.0.1 or 0.0.0.0".to_string(),
                    ));
                }
                Ok(TxSenderJsonRpcConfig { bind, port })
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

**File:** crates/clementine-tx-sender/src/cpfp.rs (L207-254)
```rust
        let funded_fee_payer_tx = self
            .rpc
            .fund_raw_transaction(
                &fee_payer_bytes,
                Some(&FundRawTransactionOptions {
                    add_inputs: Some(true),
                    // for cpfp txs, the speed of tx inclusion is not that important, so we can not use unsafe utxos and wait for them to become safe. Also all cpfp fee payer tx's are safe (all wallet owned inputs), so wallet can already chain them
                    include_unsafe: Some(self.include_unsafe),
                    change_address: None,
                    change_position: None,
                    change_type: None,
                    include_watching: None,
                    lock_unspents: None,
                    fee_rate: Some(Amount::from_sat(fee_rate.to_sat_per_kvb())),
                    subtract_fee_from_outputs: None,
                    replaceable: Some(true),
                    conf_target: None,
                    estimate_mode: None,
                }),
                None,
            )
            .await
            .wrap_err("Failed to fund cpfp fee payer tx")?
            .hex;

        let signed_fee_payer_tx: Transaction = bitcoin::consensus::deserialize(
            &self
                .rpc
                .sign_raw_transaction_with_wallet(&funded_fee_payer_tx, None, None)
                .await
                .wrap_err("Failed to sign funded tx through bitcoin RPC")?
                .hex,
        )
        .wrap_err("Failed to deserialize signed tx")?;

        let outpoint_vout = signed_fee_payer_tx
            .output
            .iter()
            .position(|o| {
                o.value == new_fee_payer_amount
                    && o.script_pubkey == self.signer.address().script_pubkey()
            })
            .ok_or(eyre!("Failed to find outpoint vout"))?;

        self.rpc
            .send_raw_transaction(&signed_fee_payer_tx)
            .await
            .wrap_err("Failed to send signed fee payer tx")?;
```

**File:** crates/clementine-tx-sender/run.sh (L35-36)
```shellscript
export TX_SENDER_JSONRPC_BIND="${TX_SENDER_JSONRPC_BIND:-127.0.0.1}"
export TX_SENDER_JSONRPC_PORT="${TX_SENDER_JSONRPC_PORT:-3030}"
```

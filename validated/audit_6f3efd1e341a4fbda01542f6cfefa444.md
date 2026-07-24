### Title
Unauthenticated JSON-RPC `send_tx` Endpoint Allows Arbitrary Transaction Enqueueing and Fee-Payer Wallet Drain — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

### Summary

The tx-sender JSON-RPC server is started with `ServerBuilder::default()` — plain HTTP, no TLS, no mTLS, no token, no middleware. When the server is bound to `0.0.0.0` (an explicitly supported and documented configuration), any network-reachable attacker can call `send_tx` with a crafted `signed_tx_hex` and `FeePayingType::CPFP`, causing the tx-sender to create fee-payer UTXOs from the operator's Bitcoin wallet to fund the attacker's transaction. This drains the operator's fee-payer balance and corrupts the `tx_sender_try_to_send_txs` dependency state.

### Finding Description

`start_jsonrpc_server` builds the server with no authentication layer: [1](#0-0) 

The `send_tx` handler performs no caller identity check — it deserializes the transaction and immediately calls `insert_try_to_send`: [2](#0-1) 

The config explicitly permits binding to `0.0.0.0`: [3](#0-2) 

The default is `127.0.0.1`, but `0.0.0.0` is a first-class, documented option. There is no authentication mechanism available regardless of which address is chosen — the code provides no way to add one.

### Impact Explanation

When `FeePayingType::CPFP` is submitted, the tx-sender loop calls `create_fee_payer_utxo`, which calls `fund_raw_transaction` against the operator's Bitcoin Core wallet to create a fee-payer UTXO: [4](#0-3) 

An attacker can repeatedly call `send_tx` with distinct valid Bitcoin transactions (each gets a unique txid, bypassing the dedup check at line 74–80 of `client.rs`), causing the operator's wallet to fund an unbounded number of fee-payer UTXOs. This:

1. **Drains the operator's fee-payer wallet** — direct loss of operator-controlled BTC used to keep bridge transactions alive.
2. **Corrupts `tx_sender_try_to_send_txs` state** — attacker-controlled cancel/activate dependency rows can interfere with legitimate bridge transaction ordering.
3. **Breaks bridge liveness** — once the fee-payer wallet is exhausted, legitimate bridge transactions (kickoff, payout, reimbursement, round) cannot be CPFP-bumped and will stall. [5](#0-4) 

### Likelihood Explanation

The vulnerability requires the server to be bound to `0.0.0.0`. This is an explicitly supported configuration (not a misconfiguration the code rejects), and is the natural choice for any deployment where the tx-sender runs as a standalone service reachable by operators/verifiers on a network. The attacker needs only TCP connectivity to the JSON-RPC port and the ability to construct a valid (but unspendable) Bitcoin transaction — no keys, no prior state, no credentials.

### Recommendation

Add an authentication layer to the JSON-RPC server. Options in order of strength:
1. **mTLS**: Wrap the `jsonrpsee` server with a TLS acceptor requiring a client certificate signed by a bridge-internal CA.
2. **Bearer token / HMAC**: Require a shared secret in a request header, validated by a `jsonrpsee` middleware layer.
3. **Localhost-only enforcement**: Reject `0.0.0.0` as a valid bind address in `TxSenderConfig::from_env` and enforce `127.0.0.1` only, with a documented requirement that callers use SSH tunnels or equivalent for remote access.

### Proof of Concept

```rust
// Plain HTTP, no certificate, no token — matches the server's ServerBuilder::default()
let client = JsonRpcTxSenderClient::new("http://<operator-ip>:<TX_SENDER_JSONRPC_PORT>").unwrap();

// Any syntactically valid Bitcoin transaction with a unique txid suffices.
// Repeat with different inputs to bypass the txid dedup check.
for i in 0..1000u32 {
    let tx = Transaction { /* inputs spending outpoint (all_zeros_txid, i) */ };
    client.insert_try_to_send(None, &tx, FeePayingType::CPFP, None, &[], &[], &[], &[]).await.unwrap();
    // tx-sender loop will call fund_raw_transaction on the operator wallet for each entry
}
// Operator's fee-payer wallet is drained; legitimate bridge txs can no longer be fee-bumped.
```

The existing test `test_jsonrpc_txsender_insert_try_to_send` already demonstrates that a plain `http://` client with no credentials successfully enqueues a transaction and confirms the row in `tx_sender_try_to_send_txs` — the only difference from the attack is the bind address. [6](#0-5)

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

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L139-142)
```rust
        let (addr, handle) = spawn_txsender_loop_with_free_localhost_jsonrpc_port(tx_sender_cfg);
        let url = format!("http://{addr}");
        let client =
            JsonRpcTxSenderClient::new(&url).map_err(|e| BridgeError::Eyre(eyre::eyre!(e)))?;
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

**File:** crates/clementine-tx-sender/src/cpfp.rs (L207-229)
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
```

**File:** crates/clementine-tx-sender/src/client.rs (L59-80)
```rust
    pub async fn insert_try_to_send(
        &self,
        dbtx: &mut crate::TxSenderTransaction,
        tx_metadata: Option<TxMetadata>,
        signed_tx: &Transaction,
        fee_paying_type: FeePayingType,
        rbf_signing_info: Option<RbfSigningInfo>,
        cancel_outpoints: &[OutPoint],
        cancel_txids: &[Txid],
        activate_txids: &[ActivatedWithTxid],
        activate_outpoints: &[ActivatedWithOutpoint],
    ) -> Result<u32, BridgeError> {
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

### Title
Unauthenticated JSON-RPC `send_tx` Endpoint Allows Unauthorized Transaction Enqueue and Bridge Fee-Payer UTXO Drain — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

---

### Summary

The tx-sender JSON-RPC server is started with no authentication, no TLS, and no middleware of any kind. Any caller that can reach the bound socket — including any process on the same host when bound to `127.0.0.1`, or any network peer when bound to `0.0.0.0` — can call `send_tx` and enqueue an arbitrary transaction with `fee_paying_type=CPFP`. The tx-sender loop will then spend bridge wallet funds to create a fee-payer UTXO for that transaction, constituting unauthorized spending of bridge-controlled funds.

---

### Finding Description

`start_jsonrpc_server` constructs the server with `ServerBuilder::default()` and binds to a plain TCP socket. There is no TLS layer, no mTLS client-certificate check, no API token, and no middleware of any kind: [1](#0-0) 

The `send_tx` handler immediately deserializes the caller-supplied `InsertTryToSendParams` and calls `TxSenderClient::insert_try_to_send` with no identity or authorization check: [2](#0-1) 

The config explicitly permits binding to `0.0.0.0`, making the server network-reachable: [3](#0-2) 

The default is `127.0.0.1`, but even that only restricts to same-host processes — there is no code-level authentication guard in either case.

---

### Impact Explanation

Once a row is inserted into `tx_sender_try_to_send_txs` with `fee_paying_type = CPFP`, the tx-sender loop calls `send_cpfp_tx`. If no confirmed fee-payer UTXOs exist for that entry (which is always the case for a freshly injected row), `create_package` returns `SendTxError::InsufficientFeePayerAmount`, and `create_fee_payer_utxo` is invoked: [4](#0-3) 

`create_fee_payer_utxo` calls `fund_raw_transaction`, `sign_raw_transaction_with_wallet`, and `send_raw_transaction` against the bridge's Bitcoin Core wallet, spending bridge wallet funds to create a fee-payer UTXO linked to the attacker's transaction: [5](#0-4) 

The attacker only needs to craft a transaction containing a P2A anchor output (`OP_1 OP_PUSHBYTES_2 0x4e73`) — a standard, freely constructable output — and submit it via `send_tx`. The attacker does not need bridge private keys. The bridge wallet funds are spent regardless of whether the attacker's transaction is valid on-chain, because fee-payer UTXO creation precedes package submission.

---

### Likelihood Explanation

- When `TX_SENDER_JSONRPC_BIND=0.0.0.0` (explicitly supported by the config), any network peer can exploit this with a single HTTP POST.
- When bound to `127.0.0.1`, any co-located process (e.g., another container sharing the host network namespace, a compromised dependency, or a local attacker) can exploit it.
- No state, credentials, or prior knowledge of bridge internals is required beyond knowing the port.

---

### Recommendation

Add an authentication layer to the JSON-RPC server before it is reachable by any caller. Options in increasing strength:

1. **Shared secret / bearer token**: Require a configurable `Authorization` header and reject requests that do not match. This is the minimum viable fix.
2. **mTLS**: Mirror the pattern used by the gRPC actors — require a client certificate signed by the bridge CA. This aligns with the existing trust model.
3. **Localhost-only + process-level isolation**: If the JSON-RPC port is only ever called by co-located Clementine actors, enforce `127.0.0.1`-only binding in code (not just as a default) and document the network isolation requirement explicitly.

---

### Proof of Concept

```rust
// Plain HTTP, no certificate, no token.
let client = reqwest::Client::new();
let p2a_tx_hex = /* consensus-serialize a Transaction with one output:
    value: 330 sat, script_pubkey: ScriptBuf::from_hex("51024e73") */;

let body = serde_json::json!({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "send_tx",
    "params": [{
        "signed_tx_hex": p2a_tx_hex,
        "fee_paying_type": "cpfp",
        "tx_metadata": null,
        "rbf_signing_info": null,
        "cancel_outpoints": [],
        "cancel_txids": [],
        "activate_txids": [],
        "activate_outpoints": []
    }]
});

let resp = client
    .post(format!("http://{addr}"))
    .json(&body)
    .send()
    .await?;

// Row now exists in tx_sender_try_to_send_txs.
// On the next tx-sender loop tick, create_fee_payer_utxo is called,
// spending bridge wallet funds to create a fee-payer UTXO for the
// attacker's transaction.
assert!(resp.json::<serde_json::Value>().await?["result"].is_number());
```

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

**File:** crates/clementine-tx-sender/src/cpfp.rs (L620-629)
```rust
            Err(SendTxError::InsufficientFeePayerAmount) => {
                self.create_fee_payer_utxo(
                    try_to_send_id,
                    None,
                    &tx,
                    fee_rate,
                    total_amount,
                    confirmed.len(),
                )
                .await?;
```

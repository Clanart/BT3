The code tells a clear story. Let me lay out the exact findings.

**`start_jsonrpc_server`** in `server.rs` uses `ServerBuilder::default()` with no middleware, no token check, no IP allowlist, and no authentication of any kind: [1](#0-0) 

The `send_tx` handler directly calls `insert_try_to_send` with fully attacker-controlled parameters — no credential check, no role check, no origin check: [2](#0-1) 

The bind address explicitly supports `0.0.0.0` as a valid production value (not just `127.0.0.1`): [3](#0-2) 

The automation loop then picks up every queued row and attempts to broadcast it, including creating CPFP child transactions spending the operator's own fee-paying UTXOs: [4](#0-3) 

---

### Title
Unauthenticated JSON-RPC `send_tx` Allows Any Network Attacker to Enqueue Arbitrary Transactions and Drain Operator Fee-Paying UTXOs — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

### Summary
`start_jsonrpc_server` exposes `send_tx` over plain HTTP with no authentication. When the server is bound to `0.0.0.0` (an explicitly supported production configuration), any TCP-reachable attacker can enqueue arbitrary Bitcoin transactions into the tx-sender queue and force the operator's fee-paying UTXOs to be spent on CPFP child transactions for attacker-chosen payloads.

### Finding Description
`ServerBuilder::default()` is used with no middleware, no bearer token, no mTLS, and no IP allowlist beyond the bind address itself. The `send_tx` RPC handler deserializes the caller-supplied `signed_tx_hex`, opens a DB transaction, and calls `TxSenderClient::insert_try_to_send` unconditionally. The attacker also controls `fee_paying_type` (CPFP or RBF), `cancel_outpoints`, and `cancel_txids`. The config parser explicitly accepts `0.0.0.0` as a valid `TX_SENDER_JSONRPC_BIND` value, making the server reachable from any host on the network in that deployment mode.

### Impact Explanation
- **Fee-paying UTXO drain**: With `FeePayingType::CPFP`, the tx-sender automation loop creates child transactions spending the operator's own wallet UTXOs to fee-bump attacker-chosen transactions. Repeated calls exhaust operator funds used to keep bridge transactions confirmed.
- **Unauthorized transaction broadcast**: Any syntactically valid Bitcoin transaction the attacker supplies is queued and broadcast by the operator's node, potentially including double-spend attempts against bridge UTXOs.
- **Bridge transaction cancellation**: The attacker can supply `cancel_outpoints` matching inputs of legitimate in-flight bridge transactions, causing the tx-sender to mark them cancelled in the DB when the attacker's transaction confirms.

### Likelihood Explanation
Exploitability requires `TX_SENDER_JSONRPC_BIND=0.0.0.0`, which is an explicitly supported and validated configuration option. No credentials, keys, or prior state are needed — a single HTTP POST suffices. The missing authentication is a code-level defect, not an operator configuration mistake.

### Recommendation
Add an authentication layer to `start_jsonrpc_server` before registering any methods. Options include:
1. A shared-secret bearer token checked in a `jsonrpsee` middleware layer.
2. Restricting the bind address to `127.0.0.1` only and removing `0.0.0.0` as a valid option.
3. mTLS at the transport layer consistent with how other Clementine actor RPCs are protected.

### Proof of Concept
```rust
// No credentials needed. Bind addr = 0.0.0.0:<port>.
let client = JsonRpcTxSenderClient::new("http://<operator-ip>:<port>").unwrap();
let tx = /* any syntactically valid Bitcoin Transaction */;
let id = client
    .insert_try_to_send(None, &tx, FeePayingType::CPFP, None, &[], &[], &[], &[])
    .await
    .unwrap();
// Row now exists in tx_sender_try_to_send_txs; automation loop will CPFP-bump it
// using the operator's own fee-paying UTXOs.
assert!(id > 0);
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

**File:** crates/clementine-tx-sender/src/task.rs (L44-49)
```rust
            .try_to_send_unconfirmed_txs(
                fee_rate,
                self.current_tip_height,
                self.last_processed_tip_height != self.current_tip_height,
            )
            .await?;
```

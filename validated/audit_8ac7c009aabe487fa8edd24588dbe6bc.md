I now have all the information needed to make a determination. Let me analyze the full attack path.

The full attack path is now clear. Here is my determination:

---

### Title
Unauthenticated `send_citrea_tx` JSON-RPC Endpoint Allows Arbitrary DA Blob Injection and tx-sender Bitcoin Wallet Drain — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

### Summary
The `send_citrea_tx` JSON-RPC method is registered with no authentication, no caller identity check, and no token/mTLS requirement. When the server is bound to `0.0.0.0` (an explicitly supported and documented configuration), any network-reachable attacker can inject arbitrary `CitreaTxRequest::BatchProof` bytes into `tx_sender_citrea_raw_tx_queue`. The tx-sender's background loop then automatically funds and broadcasts commit+reveal Bitcoin transactions for every queued row, spending the tx-sender's Bitcoin wallet on attacker-controlled blobs.

### Finding Description

`start_jsonrpc_server` builds the server with `ServerBuilder::default()` — no middleware, no auth layer: [1](#0-0) 

The `send_citrea_tx` handler is registered with zero caller validation: [2](#0-1) 

The handler calls `TxSenderClient::send_citrea_tx` directly, which inserts attacker-controlled bytes into `tx_sender_citrea_raw_tx_queue` without any identity check: [3](#0-2) 

The configuration explicitly supports binding to `0.0.0.0` (all interfaces) with no authentication requirement: [4](#0-3) 

The background task loop calls `sync_citrea_txs` on every poll cycle, which processes **all** queued rows unconditionally: [5](#0-4) 

`sync_citrea_txs` calls `fund_raw_transaction` and `send_raw_transaction` on the tx-sender's Bitcoin wallet for every queued row, including attacker-injected ones: [6](#0-5) 

The maximum request body size is 50 MB, and `MAX_CHUNK_SIZE` is 390,000 bytes, so an attacker can flood the queue with many large blobs at minimal cost to themselves: [7](#0-6) [8](#0-7) 

### Impact Explanation

Two scoped impacts are confirmed:

1. **Authorization bypass**: The invariant that only the authorized aggregator may submit Citrea DA blobs is broken. Any network-reachable party can submit arbitrary blobs when the server is bound to `0.0.0.0`.
2. **tx-sender Bitcoin wallet drain**: The tx-sender automatically funds commit transactions and broadcasts reveal transactions for every queued row. An attacker flooding the queue drains the tx-sender's Bitcoin wallet through transaction fees, potentially exhausting funds needed for legitimate bridge operations.

**The specific claim that this causes the bridge circuit to accept a fraudulent withdrawal is not supported by the scoped code.** The tx-sender only submits bytes to Bitcoin as DA blobs. Citrea's verifier is an external system that independently validates ZK proofs; an attacker cannot forge a valid ZK proof, so fraudulent withdrawal acceptance is not achievable through this path alone.

### Likelihood Explanation

- **When `TX_SENDER_JSONRPC_BIND=0.0.0.0`**: Any network-reachable attacker can exploit this with a single HTTP POST. No credentials, no state, no prior interaction required.
- **When `TX_SENDER_JSONRPC_BIND=127.0.0.1` (default)**: Exploitation requires local process access to the host (e.g., a compromised co-located service or SSRF in another local service).
- The `0.0.0.0` binding is explicitly documented and supported in the codebase, making it a realistic deployment scenario.

### Recommendation

Add caller authentication to the JSON-RPC server. Options include:
- A shared secret/bearer token validated in a `tower` middleware layer before dispatching to any method.
- Restricting the bind to `127.0.0.1` only (removing `0.0.0.0` support) and relying on OS-level process isolation.
- mTLS on the JSON-RPC listener, consistent with the rest of the Clementine actor communication model.

### Proof of Concept

```bash
# When TX_SENDER_JSONRPC_BIND=0.0.0.0 and TX_SENDER_JSONRPC_PORT=3030
curl -s -X POST http://<target>:3030 \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "send_citrea_tx",
    "params": [{
      "citrea_tx_request": {
        "BatchProof": {
          "bytes": [1,2,3,4,5,6,7,8],
          "chunk_size": null
        }
      }
    }]
  }'
# Returns {"jsonrpc":"2.0","result":<insertion_id>,"id":1}
# The tx-sender will then fund and broadcast a commit+reveal tx pair
# spending its Bitcoin wallet on the attacker's blob.
```

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L17-17)
```rust
const MAX_JSONRPC_REQUEST_BODY_SIZE: u32 = 50 * 1024 * 1024;
```

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L46-50)
```rust
    let server: Server = ServerBuilder::default()
        .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
        .build(bind_addr)
        .await
        .map_err(|e| BridgeError::Eyre(e.into()))?;
```

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L94-105)
```rust
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
```

**File:** crates/clementine-tx-sender/src/client.rs (L182-212)
```rust
    pub async fn send_citrea_tx(&self, request: CitreaTxRequest) -> Result<i64, eyre::Report> {
        use crate::citrea::data_serialization::DataOnDa;
        use crate::citrea::MAX_CHUNK_SIZE;

        let mut dbtx = self.db.begin_transaction().await?;

        let insertion_id = match request {
            CitreaTxRequest::BatchProof { bytes, chunk_size } => {
                // Hash the original proof bytes so the same proof dedupes even if callers
                // retry it with a different chunk_size or as a non-chunked Complete body.
                let full_body_hash = crate::citrea::calculate_sha256(&bytes);
                let mut chunk_size = chunk_size.unwrap_or(MAX_CHUNK_SIZE);
                if chunk_size == 0 {
                    chunk_size = MAX_CHUNK_SIZE;
                }
                if chunk_size > MAX_CHUNK_SIZE {
                    chunk_size = MAX_CHUNK_SIZE;
                }
                let chunk_size = chunk_size as usize;

                if bytes.len() <= chunk_size {
                    let data = DataOnDa::Complete(bytes);
                    let blob = borsh::to_vec(&data).expect("zk::Proof serialize must not fail");
                    self.db
                        .insert_citrea_raw_tx_single_with_hash(
                            &mut dbtx,
                            TransactionKind::Complete,
                            &blob,
                            &full_body_hash,
                        )
                        .await?
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

**File:** crates/clementine-tx-sender/src/task.rs (L36-37)
```rust
        #[cfg(feature = "citrea")]
        self.inner.sync_citrea_txs(fee_rate).await?;
```

**File:** crates/clementine-tx-sender/src/citrea/sync.rs (L467-520)
```rust
        let funded_hex = match self
            .rpc
            .fund_raw_transaction(
                &raw_bytes,
                Some(&FundRawTransactionOptions {
                    add_inputs: Some(true),
                    include_unsafe: Some(self.include_unsafe),
                    change_address: None,
                    change_position: Some(unsigned_commit_tx.output.len() as u32),
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
        {
            Ok(result) => result.hex,
            Err(e) => {
                tracing::error!(
                    insertion_id,
                    error = %e,
                    "Failed to fund commit transaction, skipping group"
                );
                return Ok(None);
            }
        };

        let signed_commit_tx = self
            .rpc
            .sign_raw_transaction_with_wallet(&funded_hex, None, None)
            .await
            .wrap_err("Failed to sign commit transaction")?
            .transaction()
            .wrap_err(
                "Failed to convert result of sign_raw_transaction_with_wallet to btc transaction",
            )?;

        let commit_txid = signed_commit_tx.compute_txid();

        if let Err(e) = self.rpc.send_raw_transaction(&signed_commit_tx).await {
            tracing::warn!(
                insertion_id,
                commit_txid = %commit_txid,
                error = %e,
                "Failed to broadcast commit transaction, skipping group"
            );
            return Ok(None);
        }
```

**File:** crates/clementine-tx-sender/src/citrea/mod.rs (L27-27)
```rust
pub(crate) const MAX_CHUNK_SIZE: u32 = 390_000;
```

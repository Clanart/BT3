Based on my full trace of the code, this is a real vulnerability. Here is the complete analysis:

---

### Title
Unauthenticated `send_citrea_tx` JSON-RPC Endpoint Allows Any Network Attacker to Inject Arbitrary DA Payloads and Drain tx-sender Bitcoin Funds — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

### Summary

The tx-sender JSON-RPC server exposes `send_citrea_tx` with zero authentication. When the server is bound to `0.0.0.0` (an explicitly supported and documented configuration option), any TCP-reachable attacker can insert arbitrary `BatchProof`, `BatchProofMethodId`, or `SequencerCommitment` payloads into `tx_sender_citrea_raw_tx_queue`. The tx-sender background loop then automatically creates and broadcasts Bitcoin commit+reveal transactions for every queued row, spending the tx-sender's own Bitcoin UTXOs to fund attacker-chosen DA submissions.

### Finding Description

`start_jsonrpc_server` builds a plain HTTP JSON-RPC server using `ServerBuilder::default()` with no middleware, no mTLS, no token, and no IP allowlist: [1](#0-0) 

The `send_citrea_tx` method is registered with no authentication check of any kind: [2](#0-1) 

This directly calls `TxSenderClient::send_citrea_tx`, which inserts the attacker-supplied payload into `tx_sender_citrea_raw_tx_queue` unconditionally: [3](#0-2) 

The config explicitly allows `0.0.0.0` as a valid bind address, making the server reachable from the network: [4](#0-3) 

On every poll iteration, `sync_citrea_txs` fetches all rows with a null `commit_outpoint` and creates Bitcoin commit+reveal transactions for them, spending the tx-sender's wallet funds: [5](#0-4) 

The gRPC mTLS interceptor that enforces aggregator/self-only access applies exclusively to the gRPC server, not to this JSON-RPC server: [6](#0-5) 

### Impact Explanation

1. **Fund drain of tx-sender Bitcoin wallet:** Every injected queue row causes the tx-sender to fund a commit transaction from its own UTXOs. An attacker can flood the queue with up to 50 MB payloads (the `MAX_JSONRPC_REQUEST_BODY_SIZE` limit), causing continuous fee expenditure until the wallet is exhausted.
2. **Unauthorized privileged DA submission:** Only the Citrea aggregator/verifier should submit DA payloads. An attacker can submit arbitrary `BatchProofMethodId` or `SequencerCommitment` bytes, polluting the Citrea DA layer with attacker-chosen data and disrupting bridge liveness.
3. **Bridge liveness disruption:** Legitimate DA submissions are delayed or starved as the tx-sender processes attacker-injected rows. [7](#0-6) 

### Likelihood Explanation

The `0.0.0.0` bind is an explicitly supported and documented configuration option (the comment on `TxSenderJsonRpcConfig` reads "Restricted to 127.0.0.1 or 0.0.0.0"). Any operator deploying the standalone tx-sender with `TX_SENDER_JSONRPC_BIND=0.0.0.0` (e.g., in a containerized environment where the service is behind a load balancer or sidecar) exposes this endpoint to the network with no authentication. [8](#0-7) 

### Recommendation

Add authentication to the JSON-RPC server. Options in order of preference:
1. **Shared secret / bearer token middleware** on the `jsonrpsee` server, checked in a `tower` layer before dispatching to any method.
2. **Restrict to `127.0.0.1` only** and remove `0.0.0.0` as a valid bind option, forcing callers to use a local Unix socket or loopback proxy.
3. **IP allowlist** passed as configuration, enforced in a request middleware layer.

The gRPC mTLS pattern already used for verifier/operator/aggregator communication (`core/src/rpc/interceptors.rs`) should be the model for this endpoint.

### Proof of Concept

```rust
// Attacker sends this HTTP POST to http://<tx-sender-host>:<port>
// with Content-Type: application/json — no credentials required.
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "send_citrea_tx",
  "params": [{
    "citrea_tx_request": {
      "BatchProofMethodId": [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,
                             16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31]
    }
  }]
}
```

The server returns `{"jsonrpc":"2.0","id":1,"result":<insertion_id>}`. On the next poll cycle, `sync_citrea_txs` picks up the row and spends tx-sender Bitcoin funds to broadcast the attacker's payload to the Citrea DA layer. [9](#0-8) [10](#0-9)

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

**File:** crates/clementine-tx-sender/src/client.rs (L182-259)
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
                } else {
                    let chunks: Vec<Vec<u8>> = bytes
                        .chunks(chunk_size)
                        .map(|chunk| {
                            borsh::to_vec(&DataOnDa::Chunk(chunk.to_vec()))
                                .expect("zk::Proof serialize must not fail")
                        })
                        .collect();
                    self.db
                        .insert_citrea_raw_tx_chunks(&mut dbtx, &chunks, &full_body_hash)
                        .await?
                }
            }
            CitreaTxRequest::BatchProofMethodId(body) => {
                if body.len() as u32 > MAX_CHUNK_SIZE {
                    return Err(eyre!(
                        "Citrea BatchProofMethodId DA payload body too large; max {} bytes",
                        MAX_CHUNK_SIZE,
                    ));
                }
                self.db
                    .insert_citrea_raw_tx_single(
                        &mut dbtx,
                        TransactionKind::BatchProofMethodId,
                        &body,
                    )
                    .await?
            }
            CitreaTxRequest::SequencerCommitment(body) => {
                if body.len() as u32 > MAX_CHUNK_SIZE {
                    return Err(eyre!(
                        "Citrea SequencerCommitment DA payload body too large; max {} bytes",
                        MAX_CHUNK_SIZE,
                    ));
                }
                self.db
                    .insert_citrea_raw_tx_single(
                        &mut dbtx,
                        TransactionKind::SequencerCommitment,
                        &body,
                    )
                    .await?
            }
        };

        self.db.commit_transaction(dbtx).await?;
        Ok(insertion_id)
```

**File:** crates/clementine-tx-sender/src/config.rs (L30-35)
```rust
pub struct TxSenderJsonRpcConfig {
    /// Bind address for the JSON-RPC server. Restricted to 127.0.0.1 or 0.0.0.0.
    pub bind: String,
    /// TCP port for the JSON-RPC server.
    pub port: u16,
}
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

**File:** crates/clementine-tx-sender/src/citrea/sync.rs (L62-106)
```rust
    pub async fn sync_citrea_txs(&self, fee_rate: FeeRateKvb) -> Result<(), eyre::Report> {
        // First, check existing commit txids for eviction.
        self.check_evicted_commit_txs().await?;

        // First get all citrea rows (except aggregate tx) with commit_outpoint IS NULL.
        // For all of these we will try to fund and create a tx that creates commit utxos.
        let citrea_rows = self
            .db
            .get_citrea_txs_with_null_commit_outpoint(None)
            .await?;

        // Group rows by insertion_id since all chunk rows share the same eventual commit tx/outpoint.
        let mut by_insertion_id: BTreeMap<i64, Vec<CitreaRawTxRow>> = BTreeMap::new();

        for row in citrea_rows {
            by_insertion_id
                .entry(row.insertion_id)
                .or_default()
                .push(row);
        }

        if !by_insertion_id.is_empty() {
            tracing::info!(
                "Found {} pending non-aggregate citrea rows across {} insertion_id groups",
                by_insertion_id.values().map(|v| v.len()).sum::<usize>(),
                by_insertion_id.len()
            );
        }

        // For each insertion_id group, create a single commit tx/outpoint shared by all rows.
        for (insertion_id, rows) in by_insertion_id {
            tracing::debug!(insertion_id, group_len = rows.len(), "Pending citrea group");

            // Build reveal scripts and collect commit addresses for all rows in this group.
            let mut rows_with_scripts = Vec::with_capacity(rows.len());

            for row in rows {
                let signing_data = self.create_reveal_script(row.transaction_kind, &row.body);
                rows_with_scripts.push((row, signing_data));
            }

            let _ = self
                .create_commit_outpoints_for_rows(fee_rate, insertion_id, rows_with_scripts)
                .await?;
        }
```

**File:** core/src/rpc/interceptors.rs (L22-33)
```rust
impl Interceptor for Interceptors {
    #[allow(clippy::result_large_err)]
    fn call(&mut self, req: Request<()>) -> Result<Request<()>, Status> {
        match self {
            Interceptors::OnlyAggregatorAndSelf {
                our_cert,
                aggregator_cert,
            } => only_aggregator_and_self(req, our_cert, aggregator_cert),
            Interceptors::Noop => Ok(req),
        }
    }
}
```

The vulnerability is real. Let me trace the full path before rendering the verdict.

**Authentication**: The JSON-RPC server is a plain HTTP server with zero authentication — no mTLS, no token, no role check. [1](#0-0) 

**`MAX_CHUNK_SIZE`** is 390,000. [2](#0-1) 

**Chunk-size clamping** only rejects `0` and values `> MAX_CHUNK_SIZE`. A value of `1` passes through unchanged. [3](#0-2) 

**No upper bound on `bytes.len()`** in the `BatchProof` arm. Compare with `BatchProofMethodId` and `SequencerCommitment`, which both explicitly reject bodies larger than `MAX_CHUNK_SIZE`. [4](#0-3) [5](#0-4) 

**Deduplication** is keyed on `SHA256(bytes)`. An attacker varying even one byte per request bypasses it entirely. [6](#0-5) 

**Chunk insertion** is unbounded: with `bytes.len() = N` and `chunk_size = 1`, exactly `N + 1` rows are written inside a single DB transaction. [7](#0-6) 

---

### Title
Missing `bytes` size bound in `BatchProof` arm allows unauthenticated DB exhaustion via `send_citrea_tx` — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`, `crates/clementine-tx-sender/src/client.rs`)

### Summary
The `send_citrea_tx` JSON-RPC endpoint has no authentication and no upper-bound check on `BatchProof.bytes`. An attacker who can reach the server can submit a single request with `bytes` near the 50 MB HTTP body limit and `chunk_size=1`, causing up to ~50 million rows to be inserted into `tx_sender_citrea_raw_tx_queue` in one transaction, exhausting the database and permanently disrupting all Citrea DA submissions.

### Finding Description
`start_jsonrpc_server` builds a plain HTTP JSON-RPC server with no authentication layer. [8](#0-7) 

The `send_citrea_tx` handler deserializes the caller-supplied `InsertCitreaRawTxParams` and immediately calls `TxSenderClient::send_citrea_tx` with no identity or authorization check. [9](#0-8) 

Inside `send_citrea_tx`, the `BatchProof` arm clamps `chunk_size` to `[1, MAX_CHUNK_SIZE]` but places **no bound on `bytes.len()`**. When `bytes.len() > chunk_size`, the code calls `bytes.chunks(chunk_size)` and collects every chunk into a `Vec<Vec<u8>>` in memory before passing it to `insert_citrea_raw_tx_chunks`. [10](#0-9) 

`insert_citrea_raw_tx_chunks` inserts one aggregate row and then one row per chunk inside a single database transaction, with no row-count limit. [11](#0-10) 

The other two `CitreaTxRequest` variants (`BatchProofMethodId`, `SequencerCommitment`) both guard against oversized bodies, making the omission in `BatchProof` a clear inconsistency. [5](#0-4) 

### Impact Explanation
A single 50 MB request with `chunk_size=1` inserts ~50,000,001 rows into `tx_sender_citrea_raw_tx_queue`. Repeated with varying bytes (to defeat SHA-256 deduplication), this exhausts PostgreSQL storage and connection resources, permanently halting all Citrea DA submissions. Because the tx-sender is responsible for posting batch proofs and sequencer commitments to Bitcoin via Citrea, this breaks bridge liveness: pending withdrawals and challenge resolutions that depend on on-chain DA cannot proceed.

### Likelihood Explanation
The JSON-RPC server is network-bound with no authentication. Any process that can reach the server's TCP port — including any co-tenant, misconfigured firewall rule, or SSRF from another service — can trigger this with a single HTTP POST. No credentials, keys, or operator role are required.

### Recommendation
1. **Bound `bytes.len()`** in the `BatchProof` arm before chunking, consistent with the other arms:
   ```rust
   if bytes.len() as u64 > MAX_BYTES_LIMIT {
       return Err(eyre!("BatchProof bytes too large"));
   }
   ```
2. **Enforce a minimum `chunk_size`** (e.g., reject `chunk_size < MIN_CHUNK_SIZE`) to prevent trivially large chunk counts even within a valid byte range.
3. **Add authentication** to the JSON-RPC server (shared secret, mTLS, or network-level restriction to localhost/internal CIDR) so only authorized callers can enqueue Citrea transactions.

### Proof of Concept
```rust
// Rust integration test sketch
#[tokio::test]
async fn test_batch_proof_bytes_unbounded() {
    let client = JsonRpcTxSenderClient::new("http://127.0.0.1:<port>").unwrap();
    // 390_001 bytes, chunk_size=1 → 390_001 chunk rows + 1 aggregate = 390_002 rows
    let result = client.send_citrea_tx(CitreaTxRequest::BatchProof {
        bytes: vec![0u8; 390_001],
        chunk_size: Some(1),
    }).await;
    // Expect rejection; instead, 390_002 rows are inserted
    let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM tx_sender_citrea_raw_tx_queue")
        .fetch_one(db.pool()).await.unwrap();
    assert!(count <= 2, "Expected bounded insertion, got {count}");
}
```

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

**File:** crates/clementine-tx-sender/src/citrea/mod.rs (L27-27)
```rust
pub(crate) const MAX_CHUNK_SIZE: u32 = 390_000;
```

**File:** crates/clementine-tx-sender/src/client.rs (L189-224)
```rust
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
```

**File:** crates/clementine-tx-sender/src/client.rs (L226-255)
```rust
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
```

**File:** crates/clementine-tx-sender/src/db/citrea.rs (L106-130)
```rust
        let insert_query = sqlx::query_scalar::<_, i64>(
            r#"
            INSERT INTO tx_sender_citrea_raw_tx_queue (transaction_kind, body, body_hash)
            VALUES ($1, $2, $3)
            ON CONFLICT (body_hash) DO NOTHING
            RETURNING insertion_id
            "#,
        )
        .bind(transaction_kind.as_i16())
        .bind(body)
        .bind(body_hash);

        if let Some(insertion_id) = insert_query.fetch_optional(&mut **tx).await? {
            return Ok((insertion_id, true));
        }

        let insertion_id = sqlx::query_scalar::<_, i64>(
            "SELECT insertion_id FROM tx_sender_citrea_raw_tx_queue WHERE body_hash = $1",
        )
        .bind(body_hash)
        .fetch_one(&mut **tx)
        .await?;

        Ok((insertion_id, false))
    }
```

**File:** crates/clementine-tx-sender/src/db/citrea.rs (L156-193)
```rust
    pub async fn insert_citrea_raw_tx_chunks(
        &self,
        tx: TxSenderDbTx<'_>,
        chunks: &[Vec<u8>],
        full_body_hash: &[u8],
    ) -> Result<i64, BridgeError> {
        if chunks.is_empty() {
            return Err(eyre::eyre!("Chunks vector cannot be empty").into());
        }

        // The aggregate row anchors deduplication for the whole proof body.
        let (insertion_id, inserted) = self
            .insert_citrea_raw_tx_with_hash_status(
                tx,
                TransactionKind::Aggregate,
                None,
                full_body_hash,
            )
            .await?;
        if !inserted {
            return Ok(insertion_id);
        }

        for chunk in chunks {
            let query = sqlx::query(
                r#"
                INSERT INTO tx_sender_citrea_raw_tx_queue (insertion_id, transaction_kind, body, body_hash)
                VALUES ($1, $2, $3, NULL)
                "#,
            )
            .bind(insertion_id)
            .bind(TransactionKind::Chunks.as_i16())
            .bind(chunk.as_slice());

            query.execute(&mut **tx).await?;
        }

        Ok(insertion_id)
```

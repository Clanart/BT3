The full attack path is confirmed. Here is the analysis:

---

### Title
Unauthenticated `send_citrea_tx` JSON-RPC Allows Unbounded Queue Flooding and Bridge Wallet Drain — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

### Summary

The `send_citrea_tx` JSON-RPC method is registered with no authentication, no rate limiting, and no queue-size cap. Any network-reachable attacker can submit an unbounded number of `CitreaTxRequest::BatchProof` payloads with distinct bytes. Each unique payload bypasses the `body_hash` deduplication and inserts new rows into `tx_sender_citrea_raw_tx_queue`. The background sync task then calls `fund_raw_transaction` + `send_raw_transaction` for every pending group, spending real Bitcoin UTXOs from the bridge wallet on attacker-controlled fake DA payloads.

### Finding Description

**Unauthenticated entrypoint.**
`start_jsonrpc_server` builds the server with `ServerBuilder::default()` and registers `send_citrea_tx` with no auth middleware, no API key, and no IP allowlist. [1](#0-0) 

**No queue-size or rate limit.**
The only per-request guard is a 50 MB HTTP body cap (`MAX_JSONRPC_REQUEST_BODY_SIZE`). There is no limit on the number of requests or total rows in `tx_sender_citrea_raw_tx_queue`. [2](#0-1) 

**Deduplication only covers identical payloads.**
`insert_citrea_raw_tx_with_hash_status` uses `ON CONFLICT (body_hash) DO NOTHING`. The `body_hash` for `BatchProof` is `SHA256(bytes)`. Submitting N payloads with distinct bytes produces N distinct hashes → N new rows, one per request. [3](#0-2) 

**Sync task creates a real Bitcoin commit transaction per pending group.**
`sync_citrea_txs` fetches all rows with `commit_outpoint IS NULL`, groups by `insertion_id`, and calls `create_commit_outpoints_for_rows` for each group. That function calls `fund_raw_transaction` (selects and locks real wallet UTXOs), `sign_raw_transaction_with_wallet`, and `send_raw_transaction` — broadcasting a real Bitcoin transaction for each attacker-injected group. [4](#0-3) [5](#0-4) 

**`BatchProof` has no payload-size guard.**
`BatchProofMethodId` and `SequencerCommitment` reject bodies larger than `MAX_CHUNK_SIZE` (390 KB). `BatchProof` has no such check — a 50 MB payload is accepted and split into ~128 chunk rows + 1 aggregate row, amplifying DB and UTXO consumption per request. [6](#0-5) [7](#0-6) 

### Impact Explanation

Each unique `BatchProof` submission causes the bridge to broadcast a real Bitcoin commit transaction spending wallet UTXOs. An attacker sending N distinct payloads drains the bridge wallet of N × (commit tx fee) satoshis. Once the wallet is exhausted, legitimate DA submissions (batch proofs, sequencer commitments) cannot be funded, breaking bridge liveness. The spent UTXOs are unrecoverable — the commit transactions are valid Bitcoin transactions confirmed on-chain.

This satisfies: *"theft, loss, permanent lock, or slashable exposure of bridged BTC, operator collateral, reimbursement outputs, bridge-controlled UTXOs, or tx-sender-managed balances"* and *"unauthorized state transition … that breaks bridge safety/liveness with material fund impact."*

### Likelihood Explanation

The JSON-RPC server is the intended external interface for Citrea node integration. If it is reachable from any non-localhost network (which the code does not prevent — `bind_addr` is operator-configured with no enforcement of localhost-only), any attacker with network access can execute this with a simple HTTP client. The attack requires no credentials, no Bitcoin, and no prior knowledge beyond the RPC method name.

### Recommendation

1. **Add authentication** to `send_citrea_tx` (and `send_tx`): shared secret / API key header checked in the handler, or mTLS at the transport layer.
2. **Add a queue-size cap**: reject inserts when `COUNT(*) FROM tx_sender_citrea_raw_tx_queue WHERE commit_outpoint IS NULL` exceeds a configured maximum.
3. **Add a per-source rate limit** at the JSON-RPC server layer.
4. **Add a `BatchProof` payload size guard** matching the `MAX_CHUNK_SIZE` check already applied to `BatchProofMethodId` and `SequencerCommitment`.
5. **Bind the JSON-RPC server to localhost** by default and document that external exposure requires explicit operator action with auth enabled.

### Proof of Concept

```python
import requests, os, json

URL = "http://<tx-sender-host>:<port>"

for i in range(1000):
    # Each payload is unique → unique body_hash → new DB row → new commit tx
    payload_bytes = list(os.urandom(32))  # 32 unique random bytes
    req = {
        "jsonrpc": "2.0",
        "method": "send_citrea_tx",
        "params": [{"citrea_tx_request": {"BatchProof": {"bytes": payload_bytes, "chunk_size": None}}}],
        "id": i,
    }
    r = requests.post(URL, json=req)
    print(i, r.json())
# After sync_citrea_txs runs, 1000 commit transactions are broadcast,
# draining the bridge wallet of 1000 × commit_tx_fee satoshis.
```

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L17-17)
```rust
const MAX_JSONRPC_REQUEST_BODY_SIZE: u32 = 50 * 1024 * 1024;
```

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L46-106)
```rust
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

**File:** crates/clementine-tx-sender/src/citrea/sync.rs (L449-533)
```rust
    async fn create_commit_outpoints_for_rows(
        &self,
        fee_rate: FeeRateKvb,
        insertion_id: i64,
        rows_with_scripts: Vec<(CitreaRawTxRow, CitreaSigningData)>,
    ) -> Result<Option<bitcoin::Txid>, eyre::Report> {
        if rows_with_scripts.is_empty() {
            return Ok(None);
        }

        let recipients: Vec<_> = rows_with_scripts
            .iter()
            .map(|(_row, signing_data)| signing_data.commit_address.clone())
            .collect();

        let unsigned_commit_tx = crate::citrea::build_commit_transaction(&recipients);
        let raw_bytes = crate::serialize_tx_for_fund_raw(&unsigned_commit_tx);

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

        for (vout, (row, _signing_data)) in rows_with_scripts.into_iter().enumerate() {
            let outpoint = bitcoin::OutPoint {
                txid: commit_txid,
                vout: vout as u32,
            };

            self.db
                .set_citrea_commit_outpoint(None, row.id, outpoint)
                .await?;
        }

        Ok(Some(commit_txid))
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

**File:** crates/clementine-tx-sender/src/citrea/mod.rs (L27-27)
```rust
pub(crate) const MAX_CHUNK_SIZE: u32 = 390_000;
```

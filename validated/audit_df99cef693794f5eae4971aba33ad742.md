### Title
Forwarded chunk parts cached without signature verification when `prev_block` is unknown, enabling chunk-cache poisoning via fake `merkle_root` injection — (File: `chain/chunks/src/shards_manager_actor.rs`)

---

### Summary

`process_partial_encoded_chunk_forward` in `ShardsManagerActor` has two cache paths: one for `Error::UnknownChunk` (header not yet seen) and one for `Error::ChainError(DBNotFoundErr)` (prev_block not yet processed, so the chunk-producer lookup fails). In the second path the signature check inside `validate_partial_encoded_chunk_forward` is the call that returns `DBNotFoundErr`, meaning the forward is stored in `chunk_forwards_cache` **before** the signature is verified. The cached parts are validated only against the forward's own `merkle_root` field, which is itself unverified. When the real chunk header later arrives, `insert_header_if_not_exists_and_process_cached_chunk_forwards` merges those parts directly into `encoded_chunks` with no re-validation, relying on a comment that incorrectly assumes the earlier validation was complete. An unprivileged network peer can exploit this to inject parts whose `merkle_root` differs from the header's `encoded_merkle_root`, causing chunk reconstruction to produce `ChunkDecodeResult::Invalid` and permanently marking the chunk as `decode_failed` in the in-memory cache.

---

### Finding Description

**Root cause — signature bypass on `DBNotFoundErr`**

`validate_partial_encoded_chunk_forward` runs three checks in order:

1. `forward.is_valid_hash()` — verifies the message's own hash field is self-consistent.
2. `validate_part(forward.merkle_root, …)` — verifies each part's Merkle proof against `forward.merkle_root`.
3. `verify_chunk_header_signature_by_hash_and_parts(…)` — resolves the chunk producer from the DB and verifies the signature. [1](#0-0) 

Step 3 requires the prev_block to be processed so the chunk-producer row exists. When it is not, the function returns `Err(near_chain::Error::DBNotFoundErr(_))`, which propagates as `Err(Error::ChainError(DBNotFoundErr))` out of `validate_partial_encoded_chunk_forward`.

**Caching without completed validation**

`process_partial_encoded_chunk_forward` catches that error and calls `insert_forwarded_chunk`: [2](#0-1) 

`insert_forwarded_chunk` stores the parts keyed by `part_ord`, overwriting any previously cached parts for the same ordinal: [3](#0-2) 

At this point `forward.merkle_root` has never been bound to the real chunk header's `encoded_merkle_root` — the header is not yet known.

**Incorrect assumption when header arrives**

When the header is later received, `insert_header_if_not_exists_and_process_cached_chunk_forwards` pops the cached parts and merges them directly into `encoded_chunks`: [4](#0-3) 

The comment at line 1530 states "we don't need any further validation for the forwarded part … the merkle root is checked against the chunk hash in the forward message." This reasoning is **only valid** for the `Error::UnknownChunk` path (where `validate_partial_encoded_chunk_forward` returned `Ok` before the header lookup failed). For the `DBNotFoundErr` path the signature was never verified, so `forward.merkle_root` is attacker-controlled and the parts are not bound to the real header.

**Reconstruction failure**

`try_process_chunk_parts_and_receipts` → `decode_encoded_chunk_if_complete` checks: [5](#0-4) 

If the injected parts were consistent with a fake `merkle_root` M′ ≠ M (the header's `encoded_merkle_root`), Reed-Solomon decoding succeeds but the root check fails, returning `ChunkDecodeResult::Invalid`. The chunk is then permanently marked `decode_failed`: [6](#0-5) 

Subsequent calls to `process_partial_encoded_chunk` for the same chunk hash return `ProcessPartialEncodedChunkResult::DecodeFailed` immediately: [7](#0-6) 

The node cannot process any block that references the poisoned chunk until it restarts.

---

### Impact Explanation

A validator node that has its `encoded_chunks` cache poisoned for a chunk it is assigned to produce or validate will:
- Fail to complete the chunk, causing missed block production.
- Fail to send a chunk endorsement, causing missed validation rewards and potential kickout.

A non-validator full node will stall on any block containing the poisoned chunk and fall behind the network tip. Recovery requires a process restart (the `decode_failed` flag is in-memory only). The corrupted value is the specific `(chunk_hash, part_ord)` entry in `encoded_chunks` whose `part` bytes are inconsistent with the header's `encoded_merkle_root`.

**Severity: High** — targeted, persistent (until restart) liveness failure on any node reachable by the attacker, with validator-slashing risk.

---

### Likelihood Explanation

The `DBNotFoundErr` window is a **normal operating condition**: any node that receives a `PartialEncodedChunkForwardMsg` before it has processed the chunk's prev_block enters this path. This is routine during block sync and at epoch boundaries. An unprivileged network peer (no validator key required) can:

1. Learn the `chunk_hash` from a block header or gossip.
2. Craft a `PartialEncodedChunkForwardMsg` with an arbitrary `merkle_root` M′, parts that are internally consistent with M′ (so `validate_part` passes), and any bytes in the `signature` field.
3. Send it to the target node while the target's prev_block is still unprocessed.

The attacker does not need to know the real chunk data or hold any stake. The only timing constraint is that the fake forward must arrive before the header is processed; this window is typically hundreds of milliseconds to seconds.

---

### Recommendation

**Option A (preferred):** In `insert_header_if_not_exists_and_process_cached_chunk_forwards`, after popping cached parts, re-validate each part against `header.encoded_merkle_root()` before merging into `encoded_chunks`. Parts that fail should be discarded and the chunk should remain in the request pool.

**Option B:** Track whether a cached forward was stored due to `DBNotFoundErr` (signature not yet verified) vs. `UnknownChunk` (signature verified). When the prev_block becomes available, re-run `validate_partial_encoded_chunk_forward` for `DBNotFoundErr`-cached forwards before merging. Discard the forward if the signature is now invalid.

**Option C:** Reject forwards outright when `verify_chunk_header_signature_by_hash_and_parts` returns `DBNotFoundErr`, and rely on the existing re-request mechanism (`resend_chunk_requests`) to fetch the parts once the prev_block is known.

---

### Proof of Concept

```
1. Attacker learns chunk_hash H (e.g., from a gossiped block header).

2. Attacker constructs PartialEncodedChunkForwardMsg:
     chunk_hash    = H
     prev_block_hash = <real prev_block_hash, publicly known>
     shard_id      = <real shard_id>
     merkle_root   = M'  (attacker-chosen, ≠ real encoded_merkle_root M)
     parts         = [num_data_parts entries, each with a valid Merkle proof
                      against M' but garbage chunk data]
     signature     = <any bytes; will not be checked>
     hash          = SHA256(serialise(above fields))  // is_valid_hash() passes

3. Attacker sends the message to the target node while the target has not yet
   processed the prev_block for H.

4. validate_partial_encoded_chunk_forward:
     is_valid_hash()                          → Ok  (hash is self-consistent)
     validate_part(M', part_i, …)             → Ok  (proofs valid against M')
     verify_chunk_header_signature_by_hash_and_parts(…)
                                              → Err(DBNotFoundErr)
                                                (prev_block not yet in DB)

5. process_partial_encoded_chunk_forward catches DBNotFoundErr →
   insert_forwarded_chunk(forward) stores fake parts in chunk_forwards_cache[H].

6. Real chunk header arrives (encoded_merkle_root = M).
   insert_header_if_not_exists_and_process_cached_chunk_forwards pops the
   cached fake parts and calls encoded_chunks.merge_in_partial_encoded_chunk
   with no re-validation.

7. try_process_chunk_parts_and_receipts → decode_encoded_chunk_if_complete:
     reed_solomon_decode succeeds (parts are consistent with M')
     computed merkle_root = M'  ≠  header.encoded_merkle_root() = M
     → ChunkDecodeResult::Invalid

8. encoded_chunks.mark_decode_failed(H) is called.
   All subsequent process_partial_encoded_chunk calls for H return
   ProcessPartialEncodedChunkResult::DecodeFailed immediately.

9. Any block referencing chunk H cannot be processed by the target node
   until it restarts and clears the in-memory encoded_chunks cache.
```

### Citations

**File:** chain/chunks/src/shards_manager_actor.rs (L1223-1227)
```rust
        let (merkle_root, merkle_paths) = chunk.content().get_merkle_hash_and_paths();
        if &merkle_root != chunk.encoded_merkle_root() {
            tracing::debug!(target: "chunks", ?merkle_root, chunk_encoded_merkle_root = ?chunk.encoded_merkle_root(), "invalid, wrong merkle root");
            return Ok(ChunkDecodeResult::Invalid(chunk));
        }
```

**File:** chain/chunks/src/shards_manager_actor.rs (L1285-1315)
```rust
    fn validate_partial_encoded_chunk_forward(
        &self,
        forward: &PartialEncodedChunkForwardMsg,
    ) -> Result<(), Error> {
        let valid_hash = forward.is_valid_hash(); // check hash

        if !valid_hash {
            return Err(Error::InvalidPartMessage);
        }

        // check part merkle proofs
        let num_total_parts = self.epoch_manager.num_total_parts();
        for part_info in &forward.parts {
            self.validate_part(forward.merkle_root, part_info, num_total_parts)?;
        }

        // check signature
        let valid_signature = verify_chunk_header_signature_by_hash_and_parts(
            self.epoch_manager.as_ref(),
            &forward.chunk_hash,
            &forward.signature,
            &forward.prev_block_hash,
            forward.shard_id,
        )?;

        if !valid_signature {
            return Err(Error::InvalidChunkSignature);
        }

        Ok(())
    }
```

**File:** chain/chunks/src/shards_manager_actor.rs (L1339-1368)
```rust
    fn insert_forwarded_chunk(&mut self, forward: PartialEncodedChunkForwardMsg) {
        let chunk_hash = forward.chunk_hash.clone();
        let num_total_parts = self.epoch_manager.num_total_parts() as u64;
        match self.chunk_forwards_cache.get_mut(&chunk_hash) {
            None => {
                // Never seen this chunk hash before, collect the parts and cache them
                let parts = forward.parts.into_iter().filter_map(|part| {
                    let part_ord = part.part_ord;
                    if part_ord > num_total_parts {
                        tracing::warn!(target: "chunks", "received chunk part with part_ord greater than the total number of chunks");
                        None
                    } else {
                        Some((part_ord, part))
                    }
                }).collect();
                self.chunk_forwards_cache.put(chunk_hash, parts);
            }

            Some(existing_parts) => {
                for part in forward.parts {
                    let part_ord = part.part_ord;
                    if part_ord > num_total_parts {
                        tracing::warn!(target: "chunks", "received chunk part with part_ord greater than the total number of chunks");
                        continue;
                    }
                    existing_parts.insert(part_ord, part);
                }
            }
        }
    }
```

**File:** chain/chunks/src/shards_manager_actor.rs (L1388-1396)
```rust
            Err(Error::ChainError(chain_error)) => {
                match chain_error {
                    near_chain::Error::DBNotFoundErr(_) => {
                        // prev_block unknown or ChunkProducers DB not populated yet —
                        // cache the forward for later validation.
                        self.insert_forwarded_chunk(forward);
                        metrics::PARTIAL_ENCODED_CHUNK_FORWARD_CACHED_WITHOUT_PREV_BLOCK.inc();
                        return Ok(()); // a normal and expected case, not error
                    }
```

**File:** chain/chunks/src/shards_manager_actor.rs (L1529-1543)
```rust
        if let Some(parts) = self.chunk_forwards_cache.pop(&header.chunk_hash()) {
            // Note that we don't need any further validation for the forwarded part.
            // The forwarded part was earlier validated via validate_partial_encoded_chunk_forward,
            // which checks the part against the merkle root in the forward message, and the merkle
            // root is checked against the chunk hash in the forward message, and that chunk hash
            // is used to identify the chunk. Furthermore, it's OK to directly use the header if
            // it is the first time we learn of the header here, because later when we call
            // try_process_chunk_parts_and_receipts, we will perform a header validation if we
            // didn't already.
            self.encoded_chunks.merge_in_partial_encoded_chunk(
                header,
                parts.into_values(),
                Vec::new().into_iter(),
            );
            return true;
```

**File:** chain/chunks/src/shards_manager_actor.rs (L1603-1613)
```rust
        if let Some(entry) = self.encoded_chunks.get(&chunk_hash) {
            if entry.complete {
                return Ok(ProcessPartialEncodedChunkResult::Known);
            }
            if entry.decode_failed {
                return Ok(ProcessPartialEncodedChunkResult::DecodeFailed);
            }
            tracing::debug!(target: "chunks", num_parts_in_cache = entry.parts.len(), total_needed = self.epoch_manager.num_data_parts(), tag_chunk_distribution = true);
        } else {
            tracing::debug!(target: "chunks", num_parts_in_cache = 0, total_needed = self.epoch_manager.num_data_parts(), tag_chunk_distribution = true);
        }
```

**File:** chain/chunks/src/shards_manager_actor.rs (L1931-1964)
```rust
                ChunkDecodeResult::Invalid(encoded_chunk) => {
                    tracing::warn!(
                        target: "chunks",
                        ?chunk_hash,
                        height = header.height_created(),
                        shard_id = %header.shard_id(),
                        %chunk_producer,
                        "chunk decode failed, poisoning cache entry",
                    );
                    metrics::CHUNK_DECODE_FAILED_TOTAL
                        .with_label_values(&[&header.shard_id().to_string()])
                        .inc();
                    // Build a PartialEncodedChunk from the cache entry's parts
                    // (which include merkle proofs) for persisting to
                    // DBCol::PartialChunks so block-syncing peers can fetch them.
                    let entry = self
                        .encoded_chunks
                        .get(&chunk_hash)
                        .expect("cache entry must exist; we just decoded from it");
                    let partial_chunk = PartialEncodedChunk::new(
                        header.clone(),
                        entry.parts.values().cloned().collect(),
                        entry.receipts.values().cloned().collect(),
                    );
                    self.encoded_chunks.mark_decode_failed(&chunk_hash);
                    self.requested_partial_encoded_chunks.remove(&chunk_hash);
                    self.client_adapter.send(
                        ShardsManagerResponse::ChunkCompleted {
                            partial_chunk,
                            decoded_chunk: DecodedChunk::Invalid(encoded_chunk),
                        }
                        .span_wrap(),
                    );
                    return Ok(ProcessPartialEncodedChunkResult::DecodeFailed);
```

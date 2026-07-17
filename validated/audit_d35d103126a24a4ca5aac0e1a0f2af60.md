### Title
Missing `shard_id` Binding Check in `set_state_header` Allows Peer-Supplied Wrong-Shard State Root to Corrupt State Sync - (File: chain/chain/src/state_sync/adapter.rs)

### Summary

`ChainStateSyncAdapter::set_state_header` validates that the peer-supplied chunk is included in the canonical block (via a merkle path), but never verifies that the chunk's `shard_id` matches the caller-requested `shard_id`. A malicious peer can supply a structurally valid header whose embedded chunk belongs to a different shard. The header passes all five validation steps and is persisted under `StateHeaderKey(requested_shard_id, sync_hash)`. Downstream, `set_state_part` reads that stored header, extracts the wrong shard's `prev_state_root`, and validates incoming state parts against it. If the attacker also supplies state parts consistent with the wrong state root, the syncing node installs the wrong shard's trie as its own, producing a permanently incorrect state root for the target shard.

### Finding Description

`set_state_header` receives `shard_id` (the shard the node is syncing) and `shard_state_header` (peer-supplied). It extracts the embedded chunk and runs five checks:

1. `validate_chunk_proofs` — internal body/header consistency  
2. `verify_path(chunk_headers_root, chunk_proof, ChunkHashHeight(chunk.chunk_hash(), chunk.height_included()))` — proves the chunk is *somewhere* in the block  
3. `verify_path` for `prev_chunk`  
4. Receipt proof chain  
5. `validate_state_root_node`

**None of these checks bind the chunk to the requested `shard_id`.** Check 2 uses `verify_path` (not `verify_path_with_index`), which only proves the chunk hash appears in the block's `chunk_headers_root` merkle tree — it does not verify the leaf's position (i.e., which shard slot it occupies). A block contains one chunk per shard, so a valid merkle path for shard 1's chunk passes `verify_path` even when the node requested shard 0.

After all checks pass, the header is stored:

```rust
let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
```

The key uses the *requested* `shard_id`, but the value contains the *wrong* shard's chunk. Later, `set_state_part` reads this stored header and extracts the state root:

```rust
let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
let chunk = shard_state_header.take_chunk();
let state_root = *chunk.take_header().take_inner().prev_state_root();
// state_root is now shard X's root, not shard Y's
self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part)
```

An attacker who also supplies state parts consistent with the wrong state root causes the node to apply shard X's trie for shard Y.

The missing check is simply:

```rust
if chunk.shard_id() != shard_id {
    return Err(Error::Other("set_shard_state failed: chunk shard_id does not match requested shard_id".into()));
}
```

### Impact Explanation

**Critical.** A syncing node that accepts a wrong-shard header will:

- Store shard X's `prev_state_root` as the authoritative root for shard Y under `StateHeaderKey(shard_Y, sync_hash)`.
- Accept state parts that are valid for shard X (attacker-controlled) and apply them as shard Y's trie via `set_state_finalize` → `apply_chunk`.
- Produce a permanently incorrect `ChunkExtra.state_root` for shard Y, causing every subsequent chunk it produces or validates for shard Y to be rejected by honest validators.
- If the node is a chunk producer for shard Y, it will be slashed or kicked out; if it is a validator, it will endorse invalid chunks.

### Likelihood Explanation

Any peer that responds to a `StateRequestHeader` message can supply a malicious header. State sync is performed by nodes catching up after epoch transitions or after being offline. The attack requires no special privilege — any reachable peer can respond to state sync requests. The attacker only needs to:

1. Respond to the syncing node's `StateRequestHeader` for shard Y with a header containing shard X's chunk (which is genuinely in the same block, so all merkle proofs are valid).
2. Respond to subsequent `StateRequestPart` requests with parts consistent with shard X's state root.

Both responses are structurally valid and pass all existing checks.

### Recommendation

Add an explicit shard binding check immediately after extracting the chunk in `set_state_header`, before any other validation:

```rust
let chunk = shard_state_header.cloned_chunk();
// NEW: bind the chunk to the requested shard
if chunk.shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::Other(format!(
        "set_shard_state failed: chunk shard_id {:?} does not match requested shard_id {:?}",
        chunk.shard_id(), shard_id
    )));
}
```

Similarly, add the analogous check for `prev_chunk_header` when it is `Some`:

```rust
if let Some(ref prev_chunk_header) = prev_chunk_header {
    if prev_chunk_header.shard_id() != shard_id {
        return Err(Error::Other("set_shard_state failed: prev_chunk shard_id mismatch".into()));
    }
}
```

### Proof of Concept

1. Node N is syncing shard 0 for `sync_hash = H`.
2. Attacker peer P intercepts the `StateRequestHeader { shard_id: 0, sync_hash: H }` message.
3. P constructs a `ShardStateSyncResponseHeader::V2` whose `chunk` field is the genuine shard 1 chunk from block `H-1`, with a valid `chunk_proof` (merkle path for shard 1's position in `chunk_headers_root`).
4. P sends this header to N.
5. N calls `set_state_header(shard_id=0, sync_hash=H, header_with_shard1_chunk)`.
6. `validate_chunk_proofs` passes (chunk body is internally consistent).
7. `verify_path(chunk_headers_root, shard1_proof, ChunkHashHeight(shard1_hash, shard1_height))` passes — shard 1's chunk IS in the block.
8. Receipt and state root node checks pass (all data is genuine for shard 1).
9. Header is stored under `StateHeaderKey(shard_id=0, sync_hash=H)` with shard 1's `prev_state_root`.
10. N calls `set_state_part(shard_id=0, ...)` → reads stored header → extracts shard 1's `state_root` → validates parts against it.
11. P supplies state parts for shard 1 (valid against shard 1's root) → `validate_state_part` passes.
12. `set_state_finalize` applies shard 1's trie as shard 0's state. Node N now has a corrupted state root for shard 0. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L368-403)
```rust
    pub fn set_state_header(
        &self,
        shard_id: ShardId,
        sync_hash: CryptoHash,
        shard_state_header: ShardStateSyncResponseHeader,
    ) -> Result<(), Error> {
        let sync_block_header = self.chain_store.get_block_header(&sync_hash)?;

        let chunk = shard_state_header.cloned_chunk();
        let prev_chunk_header = shard_state_header.cloned_prev_chunk_header();

        // 1-2. Checking chunk validity
        if !validate_chunk_proofs(&chunk, self.epoch_manager.as_ref())? {
            byzantine_assert!(false);
            return Err(Error::Other(
                "set_shard_state failed: chunk header proofs are invalid".into(),
            ));
        }

        // Consider chunk itself is valid.

        // 3. Checking that chunks `chunk` and `prev_chunk` are included in appropriate blocks
        // 3a. Checking that chunk `chunk` is included into block at last height before sync_hash
        // 3aa. Also checking chunk.height_included
        let sync_prev_block_header =
            self.chain_store.get_block_header(sync_block_header.prev_hash())?;
        if !verify_path(
            *sync_prev_block_header.chunk_headers_root(),
            shard_state_header.chunk_proof(),
            &ChunkHashHeight(chunk.chunk_hash().clone(), chunk.height_included()),
        ) {
            byzantine_assert!(false);
            return Err(Error::Other(
                "set_shard_state failed: chunk isn't included into block".into(),
            ));
        }
```

**File:** chain/chain/src/state_sync/adapter.rs (L525-532)
```rust
        // Saving the header data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();

        Ok(())
    }
```

**File:** chain/chain/src/state_sync/adapter.rs (L534-561)
```rust
    pub fn set_state_part(
        &self,
        shard_id: ShardId,
        sync_hash: CryptoHash,
        part_id: PartId,
        part: &StatePart,
    ) -> Result<(), Error> {
        let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
        let chunk = shard_state_header.take_chunk();
        let state_root = *chunk.take_header().take_inner().prev_state_root();
        if matches!(
            self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part),
            StatePartValidationResult::Invalid
        ) {
            byzantine_assert!(false);
            return Err(Error::Other(format!(
                "set_state_part failed: validate_state_part failed. state_root={:?}",
                state_root
            )));
        }
        // Saving the part data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StatePartKey(sync_hash, shard_id, part_id.idx)).unwrap();
        let bytes = part.to_bytes();
        store_update.set(DBCol::StateParts, &key, &bytes);
        store_update.commit();
        Ok(())
    }
```

**File:** chain/client/src/sync/state/downloader.rs (L44-90)
```rust
    pub fn ensure_shard_header(
        &self,
        shard_id: ShardId,
        sync_hash: CryptoHash,
        cancel: CancellationToken,
    ) -> BoxFuture<'_, Result<ShardStateSyncResponseHeader, near_chain::Error>> {
        let store = self.store.clone();
        let validation_sender = self.header_validation_sender.clone();
        let source = self.source.clone();
        let task_tracker = self.task_tracker.clone();
        let clock = self.clock.clone();
        let retry_backoff = self.retry_backoff;
        async move {
            let handle = task_tracker.get_handle(&format!("shard {} header", shard_id)).await;
            handle.set_status("Reading existing header");
            let existing_header =
                get_state_header_if_exists_in_storage(&store, sync_hash, shard_id)?;
            if let Some(header) = existing_header {
                return Ok(header);
            }

            let attempt = || {
                async {
                    let header = source
                        .download_shard_header(shard_id, sync_hash, handle.clone(), cancel.clone())
                        .await?;
                    // We cannot validate the header with just a Store. We need the Chain, so we queue it up
                    // so the chain can pick it up later, and we await until the chain gives us a response.
                    handle.set_status("Waiting for validation");
                    validation_sender
                        .send_async(
                            StateHeaderValidationRequest {
                                shard_id,
                                sync_hash,
                                header: header.clone(),
                            }
                            .span_wrap(),
                        )
                        .await
                        .map_err(|_| {
                            near_chain::Error::Other(
                                "Validation request could not be handled".to_owned(),
                            )
                        })??;
                    Ok::<ShardStateSyncResponseHeader, near_chain::Error>(header)
                }
            };
```

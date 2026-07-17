Based on my thorough code analysis, here is the finding:

---

### Title
Missing shard_id binding in `set_state_header` allows malicious peer to corrupt flat storage flat_head for a syncing shard — (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header` validates a peer-supplied `ShardStateSyncResponseHeader` but never checks that the embedded chunk's `shard_id` matches the `shard_id` parameter. A malicious peer can supply a header whose chunk belongs to shard Z while the syncing node is syncing shard Y. All existing Merkle and hash checks pass, the header is stored, and `create_flat_storage_for_shard` subsequently sets `FlatStorageReadyStatus.flat_head.hash` to `chunk_Z.prev_block()` — the wrong block — for shard Y's flat storage.

### Finding Description

**Entrypoint — `set_state_header`** [1](#0-0) 

The function performs five validation steps:

1. `validate_chunk_proofs(&chunk, ...)` — verifies the chunk's internal hash/tx/receipt consistency. No `shard_id` check.
2. `verify_path(*sync_prev_block_header.chunk_headers_root(), chunk_proof, &ChunkHashHeight(chunk.chunk_hash(), chunk.height_included()))` — proves the chunk hash is somewhere in the block's Merkle tree. The Merkle path encodes the leaf's position (shard Z's index), but **the code never asserts that this position equals the requested shard Y's index**.
3. `prev_chunk` Merkle proof — same issue.
4. Receipt proofs — validated against the `shard_id` parameter, not the chunk's embedded shard.
5. `state_root_node` — validated against `chunk_inner.prev_state_root()`, which is shard Z's root.

There is no line anywhere in `set_state_header` that checks `chunk.shard_id() == shard_id`. [2](#0-1) 

The header is then stored under `StateHeaderKey(shard_id_Y, sync_hash)` with shard Z's chunk inside.

**Propagation — `run_state_sync_for_shard`** [3](#0-2) 

`state_root = header.chunk_prev_state_root()` is shard Z's state root. State parts are downloaded and applied for shard Z's state. [4](#0-3) 

`chunk = header.cloned_chunk()` is shard Z's chunk. `create_flat_storage_for_shard` is called with it.

**Corruption — `create_flat_storage_for_shard`** [5](#0-4) 

```
flat_head_hash = *chunk.prev_block()   // chunk_Z.prev_block(), NOT chunk_Y.prev_block()
```

`FlatStorageReadyStatus { flat_head: BlockInfo { hash: flat_head_hash, ... } }` is written for `shard_uid_Y`. When `chunk_Z.height_included ≠ chunk_Y.height_included` (common when shards have different chunk inclusion patterns), `chunk_Z.prev_block() ≠ chunk_Y.prev_block()`, and shard Y's flat storage is permanently initialized with the wrong flat head.

### Impact Explanation

Shard Y's flat storage is initialized with a flat_head pointing to a block that is not the canonical predecessor for shard Y's state. Subsequent `update_flat_head` calls will encounter `FlatStorageError::BlockNotSupported` because the deltas stored during `set_state_finalize_on_height` are anchored to shard Y's correct chain, not to the block encoded in the wrong flat_head. The syncing node's shard Y state is corrupted and block processing for that shard fails. [6](#0-5) 

### Likelihood Explanation

Any peer in the NEAR network can respond to `StateRequestHeader` messages. No validator or operator privilege is required. The attacker only needs to be a reachable peer and to serve a header whose chunk is a genuine chunk from a different shard (public information). All five validation steps pass without modification because the Merkle proof for shard Z's chunk is valid against the block's `chunk_headers_root` — the missing guard is the shard-position binding.

### Recommendation

In `set_state_header`, after extracting the chunk, add an explicit check:

```rust
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

This should be placed immediately after `validate_chunk_proofs` and before the Merkle path check. [7](#0-6) 

### Proof of Concept

Build a two-shard integration test (similar to the existing pattern in `integration-tests/src/tests/client/process_blocks.rs`): [8](#0-7) 

1. Produce blocks until a sync_hash is available with two shards (shard 0 and shard 1) having different `height_included` values.
2. Obtain the legitimate header for shard 0 and the legitimate header for shard 1 from `get_state_response_header`.
3. Construct a tampered `ShardStateSyncResponseHeaderV2` for shard 0's request by substituting shard 1's `chunk` and `chunk_proof` (and matching `prev_chunk_header`/`prev_chunk_proof`).
4. Call `set_state_header(shard_id=0, sync_hash, tampered_header)` on the receiving client — assert it returns `Ok(())`.
5. Run `set_state_finalize` and observe that `FlatStorageReadyStatus.flat_head.hash` equals `chunk_1.prev_block()` rather than `chunk_0.prev_block()`.
6. Assert that a subsequent `update_flat_head` to the correct shard 0 canonical block fails with `FlatStorageError::BlockNotSupported`.

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

**File:** chain/chain/src/state_sync/adapter.rs (L525-529)
```rust
        // Saving the header data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();
```

**File:** chain/client/src/sync/state/shard.rs (L75-77)
```rust
    let header = downloader.ensure_shard_header(shard_id, sync_hash, cancel.clone()).await?;
    let state_root = header.chunk_prev_state_root();
    let num_parts = header.num_state_parts();
```

**File:** chain/client/src/sync/state/shard.rs (L214-225)
```rust
    if flat_storage_manager.get_flat_storage_for_shard(shard_uid).is_none() {
        let chunk = header.cloned_chunk();
        let block_hash = chunk.prev_block();

        // We synced shard state on top of _previous_ block for chunk in shard state header and applied state parts to
        // flat storage. Now we can set flat head to hash of this block and create flat storage.
        // If block_hash is equal to default - this means that we're all the way back at genesis.
        // So we don't have to add the storage state for shard in such case.
        // TODO(8438) - add additional test scenarios for this case.
        if *block_hash != CryptoHash::default() {
            create_flat_storage_for_shard(&store, &*runtime, shard_uid, &chunk)?;
        }
```

**File:** chain/client/src/sync/state/shard.rs (L257-290)
```rust
fn create_flat_storage_for_shard(
    store: &Store,
    runtime: &dyn RuntimeAdapter,
    shard_uid: ShardUId,
    chunk: &ShardChunk,
) -> Result<(), near_chain::Error> {
    let flat_storage_manager = runtime.get_flat_storage_manager();
    // Flat storage must not exist at this point because leftover keys corrupt its state.
    assert!(flat_storage_manager.get_flat_storage_for_shard(shard_uid).is_none());

    let flat_head_hash = *chunk.prev_block();
    let flat_head_header =
        store.get_ser::<BlockHeader>(DBCol::BlockHeader, flat_head_hash.as_bytes()).ok_or_else(
            || near_chain::Error::DBNotFoundErr(format!("No block header {}", flat_head_hash)),
        )?;
    let flat_head_prev_hash = *flat_head_header.prev_hash();
    let flat_head_height = flat_head_header.height();

    tracing::debug!(target: "store", ?shard_uid, ?flat_head_hash, flat_head_height, "set_state_finalize - initialized flat storage");

    let mut store_update = store.flat_store().store_update();
    store_update.set_flat_storage_status(
        shard_uid,
        FlatStorageStatus::Ready(FlatStorageReadyStatus {
            flat_head: near_store::flat::BlockInfo {
                hash: flat_head_hash,
                prev_hash: flat_head_prev_hash,
                height: flat_head_height,
            },
        }),
    );
    store_update.commit();
    flat_storage_manager.create_flat_storage_for_shard(shard_uid).unwrap();
    Ok(())
```

**File:** core/store/src/flat/manager.rs (L152-178)
```rust
        if let Some(flat_storage) = self.get_flat_storage_for_shard(shard_uid) {
            // Try to update flat head.
            flat_storage.update_flat_head(&new_flat_head).unwrap_or_else(|err| {
                match &err {
                    FlatStorageError::BlockNotSupported(_) => {
                        // It's possible that new head is not a child of current flat head, e.g. when we have a
                        // fork:
                        //
                        //      (flat head)        /-------> 6
                        // 1 ->      2     -> 3 -> 4
                        //                         \---> 5
                        //
                        // where during postprocessing (5) we call `update_flat_head(3)` and then for (6) we can
                        // call `update_flat_head(2)` because (2) will be last visible final block from it.
                        // In such case, just log an error.
                        tracing::debug!(
                            target: "store",
                            ?new_flat_head,
                            ?err,
                            ?shard_uid,
                            "cannot update flat head");
                    }
                    _ => {
                        // All other errors are unexpected, so we panic.
                        panic!("Cannot update flat head of shard {shard_uid:?} to {new_flat_head:?}: {err:?}");
                    }
                }
```

**File:** integration-tests/src/tests/client/process_blocks.rs (L1971-2008)
```rust
    let epoch_id = sync_prev_block.header().epoch_id();
    let shard_layout = env.clients[0].epoch_manager.get_shard_layout(&epoch_id).unwrap();
    let shard_id = shard_layout.shard_uids().next().unwrap().shard_id();

    let state_sync_header = env.clients[0]
        .chain
        .state_sync_adapter
        .get_state_response_header(shard_id, sync_hash)
        .unwrap();
    let num_parts = state_sync_header.num_state_parts();
    let state_sync_parts = (0..num_parts)
        .map(|i| {
            env.clients[0]
                .chain
                .state_sync_adapter
                .get_state_response_part(shard_id, i, sync_hash)
                .unwrap()
        })
        .collect::<Vec<_>>();

    env.clients[1]
        .chain
        .state_sync_adapter
        .set_state_header(shard_id, sync_hash, state_sync_header.clone())
        .unwrap();
    for i in 0..num_parts {
        env.clients[1]
            .chain
            .state_sync_adapter
            .set_state_part(
                shard_id,
                sync_hash,
                PartId::new(i, num_parts),
                &state_sync_parts[i as usize],
            )
            .unwrap();
    }
    {
```

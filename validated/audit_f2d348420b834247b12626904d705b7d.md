Now I have enough information to analyze the analog. Let me look at one more detail to confirm the finding.

### Title
Missing `shard_id` Binding Check in `set_state_header` Allows Cross-Shard State Header Injection - (File: chain/chain/src/state_sync/adapter.rs)

### Summary

`set_state_header` accepts a caller-supplied `shard_id` as the storage key for a downloaded `ShardStateSyncResponseHeader`, but never verifies that the chunk embedded in the header actually belongs to that shard. A malicious peer can supply a cryptographically valid header for shard X while the syncing node believes it is storing shard Y's header. The mismatch is committed to `DBCol::StateHeaders` and is then consumed verbatim by `set_state_part` and `set_state_finalize`, breaking the shard-id → state-root binding that state sync correctness depends on.

### Finding Description

`set_state_header` in `chain/chain/src/state_sync/adapter.rs` performs five validation steps before persisting the header:

1. `validate_chunk_proofs` — internal chunk consistency
2. `verify_path` against `chunk_headers_root` — proves the chunk is *somewhere* in the block
3. `verify_path` for `prev_chunk` — same
4. Receipt-proof chain — validates incoming receipts for the requested `shard_id`
5. `validate_state_root_node` — validates the state root node

None of these steps compare `chunk.shard_id()` with the `shard_id` parameter. The Merkle proof in step 2 only proves that `ChunkHashHeight(chunk.chunk_hash(), chunk.height_included())` is a leaf in the block's `chunk_headers_root`; it does not constrain which shard position that leaf occupies. A chunk for shard X at position X in the tree produces a valid proof regardless of the `shard_id` argument passed to the function.

After all checks pass, the header is committed under the wrong key:

```rust
let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
``` [1](#0-0) 

The `shard_id` parameter is never compared against `chunk.shard_id()` anywhere in the function. [2](#0-1) 

`ShardChunk` exposes `shard_id()` directly: [3](#0-2) 

### Impact Explanation

Once the poisoned header is stored, two downstream consumers read it without re-checking the shard binding:

**`set_state_part`** retrieves the header for `shard_id = Y`, extracts `state_root` from shard X's chunk, and validates incoming parts against shard X's state root. Parts for shard X's trie pass validation and are stored under shard Y's `StatePartKey`. [4](#0-3) 

**`set_state_finalize`** (via `chain_update.rs`) derives `shard_uid` from the `shard_id` parameter (Y) but calls `apply_chunk` with `chunk_header.prev_state_root()` taken from shard X's chunk. The runtime is asked to apply shard X's transactions and receipts into shard Y's trie slot using shard X's state root. [5](#0-4) 

Because the poisoned header is cached in `DBCol::StateHeaders`, the downloader's early-exit path returns it on every subsequent attempt without re-downloading: [6](#0-5) 

The syncing node is permanently wedged: it cannot complete state sync for shard Y, and it will not re-request the header from a different peer because the DB entry is treated as authoritative.

### Likelihood Explanation

State sync peers are selected from the open peer-to-peer network. Any node that can establish a connection and respond to `StateRequestHeader` messages can act as the malicious peer. No validator key or privileged role is required. The attacker only needs to serve a valid header for shard X (which it can obtain honestly from the network) and present it in response to a shard Y header request. The `validate_sync_hash` check on the serving side only verifies that the `sync_hash` belongs to a known recent epoch; it does not constrain which shard's header is returned. [7](#0-6) 

### Recommendation

Add an explicit shard-id binding check immediately after extracting the chunk from the header, before any other validation:

```rust
let chunk = shard_state_header.cloned_chunk();
if chunk.shard_id() != shard_id {
    return Err(Error::Other(format!(
        "set_shard_state failed: header chunk shard_id {} does not match \
         requested shard_id {}",
        chunk.shard_id(), shard_id
    )));
}
```

Apply the same guard in `set_state_finalize` / `chain_update::set_state_finalize` as a defence-in-depth measure.

### Proof of Concept

1. Syncing node S requests `StateRequestHeader { shard_id: Y, sync_hash }` from peer P.
2. Malicious peer P holds a valid `ShardStateSyncResponseHeader` for shard X (obtained honestly). It responds with that header.
3. S calls `set_state_header(Y, sync_hash, header_for_shard_X)`.
4. `validate_chunk_proofs` passes — shard X's chunk is internally consistent.
5. `verify_path(*sync_prev_block_header.chunk_headers_root(), chunk_proof_for_X, ChunkHashHeight(chunk_X.hash(), chunk_X.height_included()))` passes — shard X's chunk is genuinely in the block.
6. Receipt proofs for shard Y's incoming receipts are valid (P supplies them from the real block).
7. `validate_state_root_node` passes for shard X's state root.
8. No check `chunk.shard_id() == shard_id` exists; the header is stored under `StateHeaderKey(Y, sync_hash)`.
9. S subsequently calls `set_state_part(Y, sync_hash, ...)` — parts are validated against shard X's state root and stored under shard Y's key.
10. S calls `set_state_finalize(Y, sync_hash)` — `apply_chunk` is invoked with `shard_uid` for Y but `prev_state_root` from shard X's chunk, causing a trie-root-not-found failure or silent state mismatch.
11. The poisoned header remains in `DBCol::StateHeaders`; every retry reads it from cache and fails identically. Node S cannot complete state sync.

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

**File:** chain/chain/src/state_sync/adapter.rs (L534-560)
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
```

**File:** core/primitives/src/sharding.rs (L1121-1126)
```rust
    pub fn shard_id(&self) -> ShardId {
        match self {
            Self::V1(chunk) => chunk.header.inner.shard_id,
            Self::V2(chunk) => chunk.header.shard_id(),
        }
    }
```

**File:** chain/chain/src/chain_update.rs (L513-542)
```rust
        let shard_uid =
            shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
        let memtrie_pin = self
            .runtime_adapter
            .get_tries()
            .maybe_pin_memtrie_root(shard_uid, chunk_header.prev_state_root())?;
        let apply_result = self.runtime_adapter.apply_chunk(
            RuntimeStorageConfig::new(chunk_header.prev_state_root(), true),
            ApplyChunkReason::UpdateTrackedShard,
            ApplyChunkShardContext {
                shard_uid,
                gas_limit,
                last_validator_proposals: chunk_header.prev_validator_proposals(),
                is_new_chunk: true,
                on_post_state_ready: None,
                memtrie_pin,
            },
            ApplyChunkBlockContext {
                block_type: BlockType::Normal,
                height: chunk_header.height_included(),
                prev_block_hash: *chunk_header.prev_block_hash(),
                block_timestamp: block_header.raw_timestamp(),
                gas_price,
                random_seed: *block_header.random_value(),
                congestion_info: block.block_congestion_info(),
                bandwidth_requests: block.block_bandwidth_requests(),
            },
            &receipts,
            transactions,
        )?;
```

**File:** chain/client/src/sync/state/util.rs (L13-22)
```rust
pub fn get_state_header_if_exists_in_storage(
    store: &Store,
    sync_hash: CryptoHash,
    shard_id: ShardId,
) -> Result<Option<ShardStateSyncResponseHeader>, near_chain::Error> {
    Ok(store.get_ser::<ShardStateSyncResponseHeader>(
        DBCol::StateHeaders,
        &borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap(),
    ))
}
```

**File:** chain/client/src/state_request_actor.rs (L139-174)
```rust
    /// Validates sync hash and returns appropriate action to take.
    fn validate_sync_hash(&self, sync_hash: &CryptoHash) -> SyncHashValidationResult {
        match self.is_sync_hash_from_known_recent_epoch(sync_hash) {
            Ok(true) => {}
            Ok(false) => {
                tracing::info!(
                    target: "sync",
                    ?sync_hash,
                    "sync_hash didn't pass validation; belongs to an unknown epoch"
                );
                return SyncHashValidationResult::Rejected;
            }
            Err(err) => {
                tracing::warn!(target: "sync", ?err, "failed to check sync_hash epoch");
                return SyncHashValidationResult::Rejected;
            }
        }

        let good_sync_hash = match self.get_sync_hash(sync_hash) {
            Ok(sync_hash) => sync_hash,
            Err(err) => {
                tracing::debug!(target: "sync", ?err, "failed to get sync_hash for state request");
                return SyncHashValidationResult::Rejected;
            }
        };

        if good_sync_hash.as_ref() == Some(sync_hash) {
            SyncHashValidationResult::Valid
        } else {
            tracing::warn!(
                target: "sync",
                "sync_hash didn't pass validation; possible divergence in sync hash computation"
            );
            SyncHashValidationResult::Rejected
        }
    }
```

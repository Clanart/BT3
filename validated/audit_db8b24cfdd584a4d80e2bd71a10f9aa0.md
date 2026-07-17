### Title
Wrong-epoch shard layout used to index previous block's chunks in `get_state_response_part`, yielding wrong `state_root` at resharding boundary — (File: chain/chain/src/state_sync/adapter.rs)

---

### Summary

`get_state_response_part` computes `shard_index` from the **sync block's (new) epoch shard layout**, then uses that index to read `prev_state_root` from the **previous block's chunks**, which are ordered by the **old epoch's shard layout**. When dynamic resharding (V3) changes the shard ordering — e.g., shard 1 is split and removed, causing shard 2 to shift from index 2 to index 1 — the function silently reads the wrong chunk's state root. The state part is then generated from that wrong root, while the sync header (computed by `compute_state_response_header`) commits the correct root. The syncing node's validation fails, blocking state sync at every dynamic resharding boundary.

---

### Finding Description

`get_state_response_part` in `chain/chain/src/state_sync/adapter.rs` (lines 277–358) contains the following sequence:

```rust
let epoch_id = block.header().epoch_id();          // sync block's NEW epoch
let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;  // NEW layout
...
let prev_block = self.chain_store.get_block(header.prev_hash())?;   // OLD epoch block
let shard_index = shard_layout.get_shard_index(shard_id)?;          // index in NEW layout
let state_root = prev_block
    .chunks()
    .get(shard_index)                              // indexes OLD epoch chunks with NEW index
    .ok_or(Error::InvalidShardId(shard_id))?
    .prev_state_root();
``` [1](#0-0) 

The `prev_block` belongs to the previous epoch (old shard layout). Its `chunks()` array is ordered by the **old** epoch's shard layout. But `shard_index` is derived from the **new** epoch's layout. When dynamic resharding removes a parent shard and inserts two child shards with higher IDs, the remaining carried-over shards shift to lower indices in the new layout. For example:

- Old layout: `{0, 1, 2}` → shard 2 is at index 2  
- Shard 1 splits into shards 3 and 4  
- New layout: `{0, 2, 3, 4}` → shard 2 is now at index 1

`get_state_response_part` for shard 2 computes `shard_index = 1` (new layout) and reads `prev_block.chunks()[1]`, which is shard 1's chunk in the old layout. The `state_root` extracted is shard 1's pre-state root, not shard 2's.

By contrast, `compute_state_response_header` correctly uses the **previous** epoch's layout:

```rust
let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
let prev_shard_index = prev_shard_layout.get_shard_index(shard_id)?;
let chunk_header = chunks.get(prev_shard_index)...;
``` [2](#0-1) 

The two functions are inconsistent: the header commits the correct state root; the part is generated from the wrong one.

The wrong `state_root` is then passed to `obtain_state_part_impl`, which resolves `shard_uid` from `prev_prev_hash`'s epoch and attempts to read trie nodes for the wrong root under that `shard_uid`:

```rust
let epoch_id = self.epoch_manager.get_epoch_id_from_prev_block(prev_hash)?;
let shard_uid = self.get_shard_uid_from_epoch_id(shard_id, &epoch_id)?;
let trie_with_state =
    self.tries.get_trie_with_block_hash_for_shard(shard_uid, *state_root, &prev_hash, true);
``` [3](#0-2) 

The trie lookup either fails (missing trie value for the wrong root) or returns trie nodes that do not correspond to the committed state root. The syncing node validates the received part against the correct root from the header and rejects it.

---

### Impact Explanation

**High.** Any node attempting state sync at a dynamic-resharding epoch boundary requests state parts for carried-over shards whose index shifted. The serving node returns parts generated from the wrong state root. The syncing node's `validate_state_part` call (which checks against the header's committed root) rejects every part. State sync cannot complete, permanently blocking the node from joining the network after a dynamic resharding event. No existing check in `get_state_response_part` detects the index mismatch; the `shard_ids.contains(&shard_id)` guard only verifies the shard exists in the new epoch, not that the index is valid for the old epoch's chunk array.

---

### Likelihood Explanation

Triggered automatically whenever:
1. Dynamic resharding (`ProtocolFeature::DynamicResharding`) is active and a shard split occurs.
2. A node performs state sync whose `sync_hash` is the first block of the resharding epoch (epoch N+2 in the two-epoch-delay model).
3. The requesting node asks for a carried-over shard whose `ShardIndex` differs between the old and new layouts.

Condition 3 is guaranteed whenever a shard is removed from the layout (the split parent disappears, shifting all higher-ID survivors down by one index position). No privileged role or adversarial action is required; any node operator initiating state sync after a dynamic resharding event will hit this path.

---

### Recommendation

In `get_state_response_part`, derive `shard_index` from the **previous block's epoch layout**, mirroring the logic in `compute_state_response_header`:

```rust
// Replace:
let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;
let shard_index = shard_layout.get_shard_index(shard_id)?;

// With:
let prev_epoch_id = prev_block.header().epoch_id();
let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
let shard_index = prev_shard_layout.get_shard_index(shard_id)?;
```

The new-epoch layout is still needed for the `shard_ids.contains` guard (validating the requested shard exists in the epoch being synced), but the index into `prev_block.chunks()` must come from the old layout.

---

### Proof of Concept

**Setup**: Static resharding from 3 shards `{0, 1, 2}` to 4 shards `{0, 2, 3, 4}` (shard 1 splits into 3 and 4 under dynamic resharding V3). Epoch N+2 is the first epoch with the new layout.

**Trigger**:
1. A fresh node begins state sync; `sync_hash` = first block of epoch N+2.
2. The node requests state parts for shard 2 (a carried-over shard).
3. The serving node calls `get_state_response_part(shard_id=2, sync_hash)`.
4. `shard_layout` = new epoch layout `{0→0, 2→1, 3→2, 4→3}`; `shard_index = 1`.
5. `prev_block` = last block of epoch N+1 with chunks `[shard0, shard1, shard2]`.
6. `prev_block.chunks().get(1)` = shard 1's chunk header → `state_root` = shard 1's pre-state root.
7. `obtain_state_part` reads trie nodes for shard 1's root under shard 2's `shard_uid` → storage miss or wrong nodes.
8. Syncing node validates the part against the header's committed root for shard 2 → `StatePartValidationResult::Invalid`.
9. State sync for shard 2 never completes; the node cannot finalize state sync for epoch N+2. [4](#0-3) [5](#0-4)

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L93-101)
```rust
        let shard_layout = self.epoch_manager.get_shard_layout(sync_block_epoch_id)?;
        let prev_epoch_id = sync_prev_block.header().epoch_id();
        let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
        let prev_shard_index = prev_shard_layout.get_shard_index(shard_id)?;

        // Chunk header here is the same chunk header as at the `current` height.
        let sync_prev_hash = sync_prev_block.hash();
        let chunks = sync_prev_block.chunks();
        let chunk_header = chunks.get(prev_shard_index).ok_or(Error::InvalidShardId(shard_id))?;
```

**File:** chain/chain/src/state_sync/adapter.rs (L277-358)
```rust
    pub fn get_state_response_part(
        &mut self,
        shard_id: ShardId,
        part_id: u64,
        sync_hash: CryptoHash,
    ) -> Result<StatePart, Error> {
        let _span = tracing::debug_span!(
            target: "sync",
            "get_state_response_part",
            %shard_id,
            part_id,
            ?sync_hash)
        .entered();
        let block = self
            .chain_store
            .get_block(&sync_hash)
            .log_storage_error("block has already been checked for existence")?;
        let header = block.header();
        let epoch_id = block.header().epoch_id();
        // Check cache
        let key = borsh::to_vec(&StatePartKey(sync_hash, shard_id, part_id)).unwrap();
        if let Some(bytes) = self.chain_store.store_ref().get(DBCol::StateParts, &key) {
            metrics::STATE_PART_CACHE_HIT.inc();
            let state_part = StatePart::from_bytes(bytes.to_vec())?;
            return Ok(state_part);
        }
        metrics::STATE_PART_CACHE_MISS.inc();

        let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;
        let shard_ids = self.epoch_manager.shard_ids(epoch_id)?;
        if !shard_ids.contains(&shard_id) {
            return Err(shard_id_out_of_bounds(shard_id));
        }
        let prev_block = self.chain_store.get_block(header.prev_hash())?;
        let shard_index = shard_layout.get_shard_index(shard_id)?;
        let state_root = prev_block
            .chunks()
            .get(shard_index)
            .ok_or(Error::InvalidShardId(shard_id))?
            .prev_state_root();
        let prev_hash = *prev_block.hash();
        let prev_prev_hash = *prev_block.header().prev_hash();
        let state_root_node = self
            .runtime_adapter
            .get_state_root_node(shard_id, &prev_hash, &state_root)
            .log_storage_error("get_state_root_node fail")?;
        let num_parts = get_num_state_parts(state_root_node.memory_usage);
        if part_id >= num_parts {
            return Err(shard_id_out_of_bounds(shard_id));
        }
        let current_time = Instant::now();
        let state_part = self
            .runtime_adapter
            .obtain_state_part(
                shard_id,
                &prev_prev_hash,
                &state_root,
                PartId::new(part_id, num_parts),
            )
            .log_storage_error("obtain_state_part fail")?;

        let elapsed_ms = (self.clock.now().signed_duration_since(current_time))
            .whole_milliseconds()
            .max(0) as u128;
        self.requested_state_parts
            .save_state_part_elapsed(&sync_hash, &shard_id, &part_id, elapsed_ms);

        // Cache the part data, but only if the corresponding header is also cached.
        // At epoch boundaries, clear_all_downloaded_parts() deletes all cached headers
        // and parts. Since serving runs on a separate actor, a part request can arrive
        // after the clear and re-create a StatePartKey without its StateHeaderKey,
        // which the storage validator treats as an inconsistency.
        let header_key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        if self.chain_store.store_ref().exists(DBCol::StateHeaders, &header_key) {
            let mut store_update = self.chain_store.store().store_update();
            let bytes = state_part.to_bytes();
            store_update.set(DBCol::StateParts, &key, &bytes);
            store_update.commit();
        }

        Ok(state_part)
    }
```

**File:** chain/chain/src/runtime/mod.rs (L489-529)
```rust
    fn obtain_state_part_impl(
        &self,
        shard_id: ShardId,
        prev_hash: &CryptoHash,
        state_root: &StateRoot,
        part_id: PartId,
    ) -> Result<StatePart, Error> {
        let _span = tracing::debug_span!(
            target: "runtime",
            "obtain_state_part",
            part_id = part_id.idx,
            %shard_id,
            %prev_hash,
            num_parts = part_id.total)
        .entered();
        tracing::debug!(target: "state-parts", %shard_id, ?prev_hash, ?state_root, ?part_id, "obtain_state_part");

        let epoch_id = self.epoch_manager.get_epoch_id_from_prev_block(prev_hash)?;
        let shard_uid = self.get_shard_uid_from_epoch_id(shard_id, &epoch_id)?;

        let trie_with_state =
            self.tries.get_trie_with_block_hash_for_shard(shard_uid, *state_root, &prev_hash, true);

        let trie_nodes = self.tries.get_trie_nodes_for_part_from_snapshot(
            shard_uid,
            state_root,
            &prev_hash,
            part_id,
            trie_with_state,
        );
        let partial_state = match trie_nodes {
            Ok(partial_state) => partial_state,
            Err(err) => {
                tracing::error!(target: "runtime", ?err, part_id.idx, part_id.total, %prev_hash, %state_root, %shard_id, "can't get trie nodes for state part");
                return Err(err.into());
            }
        };
        let state_part =
            StatePart::from_partial_state(partial_state, self.state_parts_compression_lvl);
        Ok(state_part)
    }
```

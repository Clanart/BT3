### Title
Wrong Epoch's Shard Layout Used to Index `prev_block.chunks()` in `get_state_response_part`, Causing State Sync Failure at Resharding Boundaries - (File: chain/chain/src/state_sync/adapter.rs)

### Summary

`get_state_response_part` fetches the shard layout from the **new epoch** (`sync_block`'s epoch) and uses it to compute a `shard_index` for indexing into `prev_block.chunks()`, which is organized under the **previous epoch's** shard layout. At a resharding boundary the two layouts differ, so the wrong chunk's `prev_state_root` is read and passed to `obtain_state_part`, producing a state part that does not match the committed state root in the sync header. The sibling function `compute_state_response_header` performs the same lookup correctly by explicitly fetching `prev_shard_layout`.

### Finding Description

In `get_state_response_part` (lines 295–336 of `chain/chain/src/state_sync/adapter.rs`):

```rust
let epoch_id = block.header().epoch_id();          // ← new epoch (sync_block)
let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;  // new epoch layout
...
let prev_block = self.chain_store.get_block(header.prev_hash())?;   // last block of PREV epoch
let shard_index = shard_layout.get_shard_index(shard_id)?;          // index in NEW layout
let state_root = prev_block
    .chunks()
    .get(shard_index)          // ← indexes prev_block with the WRONG layout's index
    .ok_or(Error::InvalidShardId(shard_id))?
    .prev_state_root();
```

`sync_hash` is the first block of the new epoch; `prev_block` is the last block of the previous epoch. After a resharding event the two epochs have different `ShardLayout`s. In `ShardLayoutV2`/`V3`, shard IDs and their positional indices are decoupled, so `new_layout.get_shard_index(shard_id)` returns a position that does not correspond to the same shard in `prev_block.chunks()`.

The correct pattern is used in `compute_state_response_header` (lines 93–96 of the same file):

```rust
let prev_epoch_id = sync_prev_block.header().epoch_id();
let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
let prev_shard_index = prev_shard_layout.get_shard_index(shard_id)?;  // ← correct layout
```

The wrong `state_root` is then forwarded to `obtain_state_part` (line 332), which looks up trie nodes for a state root that either does not exist in the snapshot or belongs to a different shard, causing the call to fail or return data that fails the receiver's `validate_state_part` check against the header's committed root.

### Impact Explanation

Any node requesting state parts from a peer at a resharding epoch boundary will receive either an error response or a state part whose trie content does not match the `state_root` committed in the `ShardStateSyncResponseHeader`. The receiving node's `set_state_part` validates the part against the header root and rejects it. State sync cannot complete, permanently blocking the syncing node from joining the network after a resharding event.

### Likelihood Explanation

Dynamic resharding is a production feature of NEAR Protocol. Every resharding event creates exactly the epoch-boundary condition that triggers this bug. Any node that was offline during or after a resharding event and attempts state sync will hit this path. The `shard_id` values in the request are those of the new epoch, which are the natural values a syncing node would request; no adversarial input is required.

### Recommendation

Replace the new-epoch `shard_layout` lookup with the previous epoch's layout when computing `shard_index` for `prev_block.chunks()`, mirroring the pattern in `compute_state_response_header`:

```rust
let prev_epoch_id = prev_block.header().epoch_id();
let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
let shard_index = prev_shard_layout.get_shard_index(shard_id)?;
```

The new-epoch `shard_layout` is still needed for the `shard_ids` bounds check (line 305–309), so keep that lookup separate.

### Proof of Concept

Block chain at a resharding boundary:

```
prev_prev_block  (epoch E-1, old layout: shards [0,1])
prev_block       (epoch E-1, old layout: shards [0,1])   ← last block of old epoch
sync_block       (epoch E,   new layout: shards [0,1,2,3]) ← first block of new epoch
```

A syncing node requests `get_state_response_part(shard_id=2, sync_hash=sync_block.hash)`.

1. `epoch_id = sync_block.epoch_id()` → new epoch E.
2. `shard_layout = get_shard_layout(epoch_id)` → new layout with 4 shards; `shard_id=2` has `shard_index=2`.
3. `prev_block.chunks().get(2)` → accesses the chunk at position 2 in `prev_block`, but `prev_block` only has 2 chunks (indices 0 and 1 under the old layout). Returns `None` → `Error::InvalidShardId`.

Even if the index is in range, the chunk at that position belongs to a different shard under the old layout, so `state_root` is wrong and `obtain_state_part` either errors or produces a part that fails `validate_state_part` on the receiver. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L93-96)
```rust
        let shard_layout = self.epoch_manager.get_shard_layout(sync_block_epoch_id)?;
        let prev_epoch_id = sync_prev_block.header().epoch_id();
        let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
        let prev_shard_index = prev_shard_layout.get_shard_index(shard_id)?;
```

**File:** chain/chain/src/state_sync/adapter.rs (L295-336)
```rust
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
```

**File:** chain/chain/src/types.rs (L650-659)
```rust
    /// Get part of the state corresponding to the given state root.
    /// `prev_hash` is a block whose post state root is `state_root`.
    /// Returns error when storage is inconsistent.
    fn obtain_state_part(
        &self,
        shard_id: ShardId,
        prev_hash: &CryptoHash,
        state_root: &StateRoot,
        part_id: PartId,
    ) -> Result<StatePart, Error>;
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

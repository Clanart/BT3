### Title
State-Part Serving Uses New-Epoch Shard Index to Index Old-Epoch Chunk Array at Resharding Boundary — (`File: chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`get_state_response_part` derives `shard_index` from the **new epoch's** shard layout and then uses it to index into `prev_block.chunks()`, which is ordered by the **old epoch's** shard layout. After a resharding event the two layouts have independent index-to-shard-id mappings (ShardLayoutV2/V3 explicitly documents that shard-id ≠ shard-index). The result is that serving a state-part request for a child shard at a resharding boundary either returns the wrong `prev_state_root` (pointing to a different shard's trie) or returns `Error::InvalidShardId`, making state sync permanently unavailable for that shard until the cache is populated by another path.

---

### Finding Description

`get_state_response_part` in `chain/chain/src/state_sync/adapter.rs` (lines 277–358) computes the state root it will use to generate a state part as follows:

```rust
let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;   // NEW epoch layout
// ...
let prev_block = self.chain_store.get_block(header.prev_hash())?;     // LAST block of OLD epoch
let shard_index = shard_layout.get_shard_index(shard_id)?;            // index in NEW layout
let state_root = prev_block
    .chunks()
    .get(shard_index)                                                  // applied to OLD layout's array
    .ok_or(Error::InvalidShardId(shard_id))?
    .prev_state_root();
``` [1](#0-0) 

`sync_hash` is the first block of the new epoch. `epoch_id` is therefore the new epoch. `prev_block` is the last block of the old epoch, whose `chunks()` array is ordered by the **old** shard layout.

In `ShardLayoutV2` and `ShardLayoutV3`, shard-id and shard-index are explicitly **not** the same:

```rust
// In V2 & V3 the shard id and shard index are **not** the same.
Self::V2(v2) => v2.get_shard_index(shard_id),
Self::V3(v3) => v3.get_shard_index(shard_id),
``` [2](#0-1) 

When shard 1 (old layout: `[0,1]`) splits into children `[2,3]`, the new layout's `id_to_index_map` becomes `{0:0, 2:1, 3:2}`. The old layout's `chunks()` array has only two entries (indices 0 and 1):

- **Child shard 2** → new `shard_index = 1` → `prev_block.chunks().get(1)` = parent shard 1's chunk → **wrong state root** (parent's trie, not the child's)
- **Child shard 3** → new `shard_index = 2` → `prev_block.chunks().get(2)` = `None` → **`Error::InvalidShardId`** → state sync permanently blocked for this shard

The sibling function `compute_state_response_header` correctly handles this by calling `get_prev_shard_id_from_prev_hash`, which maps the child shard to its parent and uses the parent's index in the **old** layout:

```rust
if is_resharding_boundary {
    let parent_shard_id = shard_layout.get_parent_shard_id(shard_id)?;
    let parent_shard_index = prev_shard_layout.get_shard_index(parent_shard_id)?;
    Ok((prev_shard_layout, parent_shard_id, parent_shard_index))
}
``` [3](#0-2) 

`get_state_response_part` omits this translation entirely. [4](#0-3) 

---

### Impact Explanation

**High.** State sync is the mechanism by which validators catch up to a new shard they must track in the next epoch. If `get_state_response_part` returns `InvalidShardId` for a child shard, every requesting node retrying against every serving node hits the same code path and receives the same error. State sync for that child shard is permanently blocked until the `DBCol::StateParts` cache is populated by an alternative path (e.g., a node that computed the part before the resharding boundary). If the wrong `prev_state_root` is returned instead, the generated state part will fail `validate_state_part` on the receiving side, also blocking sync. Validators unable to complete state sync cannot produce or validate chunks for the new shard in the next epoch, forfeiting rewards and potentially reducing the validator set below safety thresholds for that shard.

---

### Likelihood Explanation

Triggered at every resharding epoch boundary when any node performs state sync for a child shard. Resharding is a planned, recurring protocol event. The second child shard (higher index) is always affected; the first child shard receives the parent's state root (which may be acceptable as a starting point but is semantically incorrect). No special attacker capability is required — any node legitimately requesting state sync triggers the bug on the serving node.

---

### Recommendation

In `get_state_response_part`, replace the direct `shard_layout.get_shard_index(shard_id)` call with the same resharding-aware translation used by `compute_state_response_header`. Concretely, call `get_prev_shard_id_from_prev_hash` (or inline its logic) to obtain the correct `(prev_shard_layout, prev_shard_id, prev_shard_index)` triple, then use `prev_shard_index` to index into `prev_block.chunks()`:

```rust
// Replace:
let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;
let shard_index = shard_layout.get_shard_index(shard_id)?;
let state_root = prev_block.chunks().get(shard_index)...prev_state_root();

// With:
let (prev_shard_layout, prev_shard_id, prev_shard_index) =
    self.epoch_manager.get_prev_shard_id_from_prev_hash(header.prev_hash(), shard_id)?;
let state_root = prev_block.chunks().get(prev_shard_index)...prev_state_root();
// Use prev_shard_id when calling obtain_state_part
```

---

### Proof of Concept

1. Configure a two-shard network (shards `[0,1]`) with `ShardLayoutV2`.
2. Trigger resharding: shard 1 splits into children `[2,3]` at epoch boundary.
3. Start a new node that needs to state-sync for the new epoch.
4. The node sends `StateRequestPart { shard_id: ShardId(3), sync_hash, part_id: 0 }`.
5. The serving node enters `get_state_response_part`:
   - `epoch_id` = new epoch; `shard_layout` = `{0:0, 2:1, 3:2}`
   - `shard_index = shard_layout.get_shard_index(ShardId(3))` = `2`
   - `prev_block.chunks().get(2)` = `None` (old layout has only 2 chunks)
   - Returns `Error::InvalidShardId(ShardId(3))`
6. The requesting node receives an error and retries indefinitely; state sync for shard 3 never completes. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** core/primitives/src/shard_layout/mod.rs (L374-382)
```rust
    pub fn get_shard_index(&self, shard_id: ShardId) -> Result<ShardIndex, ShardLayoutError> {
        match self {
            // In V0 & V1 the shard id and shard index are the same.
            Self::V0(_) | Self::V1(_) => Ok(shard_id.into()),
            // In V2 & V3 the shard id and shard index are **not** the same.
            Self::V2(v2) => v2.get_shard_index(shard_id),
            Self::V3(v3) => v3.get_shard_index(shard_id),
        }
    }
```

**File:** chain/epoch-manager/src/adapter.rs (L279-297)
```rust
    fn get_prev_shard_id_from_prev_hash(
        &self,
        prev_hash: &CryptoHash,
        shard_id: ShardId,
    ) -> Result<(ShardLayout, ShardId, ShardIndex), EpochError> {
        let shard_layout = self.get_shard_layout_from_prev_block(prev_hash)?;
        let prev_shard_layout = self.get_shard_layout(&self.get_epoch_id(prev_hash)?)?;
        let is_resharding_boundary =
            self.is_next_block_epoch_start(prev_hash)? && prev_shard_layout != shard_layout;

        if is_resharding_boundary {
            let parent_shard_id = shard_layout.get_parent_shard_id(shard_id)?;
            let parent_shard_index = prev_shard_layout.get_shard_index(parent_shard_id)?;
            Ok((prev_shard_layout, parent_shard_id, parent_shard_index))
        } else {
            let shard_index = shard_layout.get_shard_index(shard_id)?;
            Ok((shard_layout, shard_id, shard_index))
        }
    }
```

**File:** core/primitives/src/shard_layout/v2.rs (L232-262)
```rust
    pub fn derive(base_shard_layout: &ShardLayout, new_boundary_account: AccountId) -> Self {
        let mut boundary_accounts = base_shard_layout.boundary_accounts().clone();
        let mut shard_ids = base_shard_layout.shard_ids().collect_vec();
        let mut shards_split_map = shard_ids
            .iter()
            .map(|id| (*id, vec![*id]))
            .collect::<BTreeMap<ShardId, Vec<ShardId>>>();

        assert!(!boundary_accounts.contains(&new_boundary_account), "duplicated boundary account");

        // boundary accounts should be sorted such that the index points to the shard to be split
        boundary_accounts.push(new_boundary_account.clone());
        boundary_accounts.sort();
        let new_boundary_account_index = boundary_accounts
            .iter()
            .position(|acc| acc == &new_boundary_account)
            .expect("account should be guaranteed to exist at this point");

        // new shard ids start from the current max
        let max_shard_id =
            *shard_ids.iter().max().expect("there should always be at least one shard");
        let new_shards = vec![max_shard_id + 1, max_shard_id + 2];
        let parent_shard_id = shard_ids
            .splice(new_boundary_account_index..new_boundary_account_index + 1, new_shards.clone())
            .collect_vec();
        let [parent_shard_id] = parent_shard_id.as_slice() else {
            panic!("should only splice one shard");
        };
        shards_split_map.insert(*parent_shard_id, new_shards);

        Self::new(boundary_accounts, shard_ids, Some(shards_split_map))
```

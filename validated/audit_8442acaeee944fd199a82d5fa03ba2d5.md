### Title
Wrong Epoch Shard Layout Used to Derive State Root in `get_state_response_part`, Breaking State-Part Commitment During Resharding - (File: `chain/chain/src/state_sync/adapter.rs`)

### Summary

`get_state_response_part` resolves the shard index using the **new epoch's** shard layout, then uses that index to look up the state root from the **previous epoch's** block chunks. `compute_state_response_header` correctly uses the **previous epoch's** shard layout for the same lookup. During resharding (V2/V3 shard layouts), the same `shard_id` maps to a different index in the two layouts, so the state root embedded in the header and the state root used to generate parts diverge. Every state part served for an affected shard fails the receiver's `validate_state_part` check, permanently blocking state sync for those shards in a resharding epoch.

### Finding Description

`compute_state_response_header` (lines 93–101) correctly resolves the shard index against the **previous epoch's** layout before indexing into `sync_prev_block.chunks()`:

```rust
let prev_epoch_id = sync_prev_block.header().epoch_id();
let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
let prev_shard_index = prev_shard_layout.get_shard_index(shard_id)?;
let chunk_header = chunks.get(prev_shard_index)...;
``` [1](#0-0) 

`get_state_response_part` (lines 305–316) instead resolves the shard index against the **new epoch's** layout (`epoch_id = sync_block.header().epoch_id()`), then uses that index to read `prev_block.chunks()` — the same previous-epoch block:

```rust
let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?; // new epoch
...
let shard_index = shard_layout.get_shard_index(shard_id)?;         // new-epoch index
let state_root = prev_block.chunks().get(shard_index)...prev_state_root(); // wrong slot
``` [2](#0-1) 

In `ShardLayoutV2::derive` and `ShardLayoutV3`, when a parent shard is split, the parent is **removed** and two new child shards are **spliced in at the parent's position**. All shards that were positioned after the split point shift by one index. The `id_to_index_map` in V2/V3 stores these per-layout positions explicitly: [3](#0-2) [4](#0-3) 

Concrete example from the test suite — old layout `[1, 3, 4]` (indices 0, 1, 2) splits shard 1 into shards 5 and 6, producing new layout `[5, 6, 3, 4]` (indices 0, 1, 2, 3): [5](#0-4) 

For `shard_id = 3`:
- `prev_shard_layout.get_shard_index(3)` → **1** (correct slot in prev block)
- `shard_layout.get_shard_index(3)` → **2** (wrong slot in prev block)

`compute_state_response_header` reads `prev_block.chunks()[1].prev_state_root()` → **state_root_A** (stored in the header).  
`get_state_response_part` reads `prev_block.chunks()[2].prev_state_root()` → **state_root_B** (used to generate parts).

`state_root_A ≠ state_root_B`.

The receiver's `set_state_part` validates every part against the header's state root: [6](#0-5) 

Because the part was generated under `state_root_B`, `validate_state_part(shard_id, &state_root_A, part_id, part)` returns `Invalid` for every part, and state sync is permanently broken for the affected shard.

### Impact Explanation

Any node attempting state sync at a resharding epoch boundary cannot complete synchronization for shards whose index shifts in the new layout. Since state sync is the only mechanism for a node to catch up after falling behind by more than one epoch, this renders those nodes permanently unable to participate in the network for the affected shards. The failure is deterministic and affects every serving node equally — there is no fallback.

### Likelihood Explanation

The bug is triggered by the combination of (a) a resharding event (shard layout V2/V3 with a split) and (b) a node requesting state sync at that epoch boundary. Both conditions are expected in production: resharding is a planned protocol feature, and state sync is the standard catch-up mechanism. The affected shards are those that were not split but whose index shifted because a preceding shard was split — typically the majority of shards in the layout.

### Recommendation

In `get_state_response_part`, resolve the shard index using the **previous epoch's** shard layout (the epoch of `prev_block`), mirroring the logic in `compute_state_response_header`:

```rust
// Replace:
let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;
// With:
let prev_epoch_id = prev_block.header().epoch_id();
let shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
```

The `shard_ids` membership check should also use the previous epoch's layout, since the state root being served belongs to the previous epoch's state.

### Proof of Concept

1. Configure a two-epoch test with a resharding event: old layout `[shard_1, shard_3, shard_4]`, new layout `[shard_5, shard_6, shard_3, shard_4]` (shard_1 split into shard_5 and shard_6).
2. Advance the chain past the epoch boundary so the sync hash is the first block of the new epoch.
3. Call `compute_state_response_header(shard_3, sync_hash)` — this returns a header whose `chunk_prev_state_root` is `prev_block.chunks()[1].prev_state_root()` (index 1 in old layout).
4. Call `get_state_response_part(shard_3, 0, sync_hash)` — this generates a part from `prev_block.chunks()[2].prev_state_root()` (index 2 in new layout, a different chunk).
5. Call `set_state_part(shard_3, sync_hash, PartId::new(0, num_parts), &part)` on the receiving node — `validate_state_part` fails because the part's trie root does not match the header's committed state root, returning `set_state_part failed: validate_state_part failed`. [7](#0-6)

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

**File:** chain/chain/src/state_sync/adapter.rs (L305-316)
```rust
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

**File:** core/primitives/src/shard_layout/v2.rs (L210-215)
```rust
        let mut id_to_index_map = BTreeMap::new();
        let mut index_to_id_map = BTreeMap::new();
        for (shard_index, &shard_id) in shard_ids.iter().enumerate() {
            id_to_index_map.insert(shard_id, shard_index);
            index_to_id_map.insert(shard_index, shard_id);
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

**File:** core/primitives/src/shard_layout/tests.rs (L277-290)
```rust
    // ["test1", "test3"] -> ["test0", "test1", "test3"]
    // [(1, [5, 6]), (3, [3]), (4, [4])]
    // [1, 3, 4] -> [5, 6, 3, 4]
    let base_layout = derived_layout;
    let boundary: AccountId = "test0.near".parse().unwrap();
    let derived_layout = ShardLayout::derive_shard_layout(&base_layout, boundary);
    assert_eq!(
        derived_layout,
        ShardLayout::v2(
            to_boundary_accounts(["test0.near", "test1.near", "test3.near"]),
            to_shard_ids([5, 6, 3, 4]),
            Some(to_shards_split_map([(1, vec![5, 6]), (3, vec![3]), (4, vec![4]),])),
        ),
    );
```

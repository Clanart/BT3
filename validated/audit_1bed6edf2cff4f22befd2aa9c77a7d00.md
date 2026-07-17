### Title
Wrong Epoch's Shard Layout Used to Index `prev_block.chunks()` in `get_state_response_part` — (File: chain/chain/src/state_sync/adapter.rs)

---

### Summary

`get_state_response_part` resolves `shard_index` using the **new epoch's** shard layout, then uses that index to read `prev_state_root` from `prev_block.chunks()`, which is a block belonging to the **old epoch** and therefore indexed by the **old epoch's** shard layout. At a resharding boundary, unchanged shards can have different indices in the two layouts. This causes the serving node to read the wrong `state_root`, producing a state part that is inconsistent with the committed `state_root` in the header (which `compute_state_response_header` computes correctly using `prev_shard_layout`). The receiver's `validate_state_part` call then rejects the part, permanently breaking state sync for any unchanged shard whose index shifts across the resharding.

---

### Finding Description

**`compute_state_response_header`** (lines 93–101) correctly resolves the shard index using the *previous* epoch's layout before indexing into `sync_prev_block.chunks()`:

```rust
let prev_epoch_id = sync_prev_block.header().epoch_id();
let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
let prev_shard_index = prev_shard_layout.get_shard_index(shard_id)?;
let chunk_header = chunks.get(prev_shard_index)...
```

**`get_state_response_part`** (lines 305–316) instead resolves `shard_index` from the *new* epoch's layout, then uses it to index into `prev_block.chunks()` — a block that belongs to the old epoch:

```rust
let epoch_id = block.header().epoch_id();          // new epoch
let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;  // new layout
...
let prev_block = self.chain_store.get_block(header.prev_hash())?;   // old epoch block
let shard_index = shard_layout.get_shard_index(shard_id)?;          // index in NEW layout
let state_root = prev_block
    .chunks()
    .get(shard_index)                                                // but chunks use OLD layout!
    .ok_or(Error::InvalidShardId(shard_id))?
    .prev_state_root();
```

`ShardLayoutV2::derive` (v2.rs lines 232–262) splices the two child shards into the position of the parent shard in the `shard_ids` vector, shifting every shard that follows the split point to a higher index. The `derive_layout` test (tests.rs lines 277–290) confirms this concretely:

```
Old layout shard_ids: [5, 6, 3, 4]   → shard 4 is at index 3
New layout shard_ids: [5, 6, 7, 8, 4] → shard 4 is at index 4
```

The sticky-resharding comment (sticky_resharding.rs line 302) also explicitly states: *"unchanged shards keep their assignment by `ShardId` even though shard indices shift."*

So for shard 4 at this resharding boundary:
- `shard_layout.get_shard_index(ShardId::new(4))` → **4** (new layout)
- `prev_block.chunks().get(4)` → **`None`** (old layout has only 4 chunks, indices 0–3)
- `get_state_response_part` returns `Error::InvalidShardId(shard_id)` — state sync is broken

Even in the case where the index is in-bounds but points to a different shard's chunk, the wrong `prev_state_root` is extracted, producing a state part that fails `validate_state_part` on the receiver side (which validates against the correct root from the header).

---

### Impact Explanation

Any validator or full node that needs to state-sync an **unchanged shard** at a resharding epoch boundary cannot obtain valid state parts from any peer running this code. The serving node either returns an error (index out of bounds) or returns a part built from the wrong trie root, which the receiver's `validate_state_part` rejects. Because the bug is in the serving path and all nodes share the same code, no peer can serve a valid part. Validators newly assigned to track an unchanged shard in the post-resharding epoch cannot complete state sync, cannot apply chunks for that shard, and will be kicked out for missing chunk production — a High-severity liveness impact at every resharding boundary.

---

### Likelihood Explanation

The bug is deterministic and fires at every resharding event (static or dynamic) where at least one unchanged shard's index shifts in the new layout. `ShardLayoutV2::derive` always inserts the two child shards at the parent's position, shifting all subsequent shards. This is the normal, expected behavior of every resharding. The bug therefore fires unconditionally at every resharding boundary for any shard positioned after the split point.

---

### Recommendation

In `get_state_response_part`, resolve `shard_index` from the **previous epoch's** shard layout (matching what `compute_state_response_header` does), not from the new epoch's layout:

```rust
// Replace:
let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;
...
let shard_index = shard_layout.get_shard_index(shard_id)?;

// With:
let prev_epoch_id = prev_block.header().epoch_id();
let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
let shard_index = prev_shard_layout.get_shard_index(shard_id)?;
```

This mirrors the logic already present in `compute_state_response_header` and ensures the chunk index used to read `prev_state_root` matches the layout under which `prev_block` was produced.

---

### Proof of Concept

Using the layout sequence from `derive_layout` test (core/primitives/src/shard_layout/tests.rs lines 277–290):

1. Old layout: `shard_ids = [5, 6, 3, 4]` — shard 4 is at **index 3**.
2. Resharding splits shard 3 → children 7, 8. New layout: `shard_ids = [5, 6, 7, 8, 4]` — shard 4 is at **index 4**.
3. `sync_block` = first block of new epoch; `prev_block` = last block of old epoch (4 chunks, indices 0–3).
4. A peer requests `StateRequestPart { shard_id: 4, sync_hash, part_id: 0 }`.
5. `get_state_response_part` computes `shard_index = shard_layout.get_shard_index(4)` → **4**.
6. `prev_block.chunks().get(4)` → **`None`** (only 4 chunks exist).
7. Returns `Error::InvalidShardId(4)`.
8. The requesting node retries indefinitely; state sync for shard 4 never completes.
9. `compute_state_response_header` would have used `prev_shard_layout.get_shard_index(4)` → **3**, reading the correct chunk and the correct `prev_state_root`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** core/primitives/src/shard_layout/tests.rs (L277-305)
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

    // ["test0", "test1", "test3"] -> ["test0", "test1", "test2", "test3"]
    // [(5, [5]), (6, [6]), (3, [7, 8]), (4, [4])]
    // [5, 6, 3, 4] -> [5, 6, 7, 8, 4]
    let base_layout = derived_layout;
    let boundary: AccountId = "test2.near".parse().unwrap();
    let derived_layout = ShardLayout::derive_shard_layout(&base_layout, boundary);
    assert_eq!(
        derived_layout,
        ShardLayout::v2(
            to_boundary_accounts(["test0.near", "test1.near", "test2.near", "test3.near"]),
            to_shard_ids([5, 6, 7, 8, 4]),
            Some(to_shards_split_map([(5, vec![5]), (6, vec![6]), (3, vec![7, 8]), (4, vec![4]),])),
        )
    );
```

**File:** chain/epoch-manager/src/shard_assignment/sticky_resharding.rs (L298-303)
```rust
    #[test]
    /// One shard splits into two: parent's validators are partitioned across
    /// the children with a stake-balanced bin-packing, and the unchanged
    /// shards keep their assignment by `ShardId` even though shard indices
    /// shift.
    fn test_sticky_resharding_simple_split() {
```

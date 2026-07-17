### Title
`get_state_response_part` Uses Sync-Epoch Shard Layout to Index Prev-Epoch Block Chunks, Producing Wrong State Root at Resharding Boundaries — (`chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`get_state_response_part` derives `shard_index` from the **sync block's epoch** shard layout and uses it to index into the **previous block's** chunk array. `compute_state_response_header` — which produces the authoritative state root committed in the header — correctly derives `shard_index` from the **previous epoch's** shard layout. At a resharding boundary (ShardLayoutV2/V3), unchanged shards can occupy a different position in the new epoch's layout than in the old epoch's layout. The two functions therefore read different chunks from the same block, producing a state root mismatch: the header commits the correct root while the part-serving path generates parts from the wrong root. Every part served for an affected shard fails the client's validation check, permanently stalling state sync for those shards at the resharding epoch.

---

### Finding Description

**`compute_state_response_header`** (the header path) correctly resolves the shard index against the **previous epoch's** layout:

```rust
// adapter.rs lines 93-101
let shard_layout = self.epoch_manager.get_shard_layout(sync_block_epoch_id)?;
let prev_epoch_id = sync_prev_block.header().epoch_id();
let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
let prev_shard_index = prev_shard_layout.get_shard_index(shard_id)?;
// ...
let chunk_header = chunks.get(prev_shard_index)...   // ← correct prev-epoch index
```

**`get_state_response_part`** (the part-serving path) resolves the shard index against the **sync (new) epoch's** layout, then uses that index to read from the same previous block:

```rust
// adapter.rs lines 305-316
let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;  // NEW epoch
// ...
let prev_block = self.chain_store.get_block(header.prev_hash())?;   // OLD epoch block
let shard_index = shard_layout.get_shard_index(shard_id)?;          // NEW epoch index ← wrong
let state_root = prev_block
    .chunks()
    .get(shard_index)                                                // wrong chunk
    .ok_or(Error::InvalidShardId(shard_id))?
    .prev_state_root();
```

`ShardStateSyncResponseHeader::num_state_parts()` derives `num_parts` from the header's `state_root_node.memory_usage`:

```rust
// core/primitives/src/state_sync.rs line 240-242
pub fn num_state_parts(&self) -> u64 {
    get_num_state_parts(self.state_root_node().memory_usage)
}
```

The header's `state_root_node` is computed from the **correct** prev-epoch chunk. The part-serving path computes `num_parts` from a **different** state root node (wrong chunk), so both the state root and the part-count divisor diverge from the committed header values — an exact analog to the ERC4626 "wrong decimals as divisor" pattern.

**Concrete resharding scenario (ShardLayoutV2 `derive`):**

```
Old epoch: shard_ids = [A, B, C]  →  indices [0, 1, 2]
New epoch: shard_ids = [A, X, Y, C]  →  indices [0, 1, 2, 3]
           (B split into X, Y; C shifts from index 2 → 3)
```

For shard C (unchanged, index shifted):
- Header path: `prev_shard_layout.get_shard_index(C)` = 2 → correct `prev_state_root` for C
- Part path: `shard_layout.get_shard_index(C)` = 3 → `prev_block.chunks().get(3)` = `None` → `Error::InvalidShardId`

For shard A (unchanged, index stable at 0): no mismatch.

For shard B (unchanged, index shifted from 1 → 2):
- Part path: `shard_layout.get_shard_index(B)` = 2 → reads chunk for old shard C → wrong `state_root`
- Parts generated from C's trie root; client validates against B's root from header → `StatePartValidationResult::Invalid` on every attempt

The `set_state_part` validation path confirms the client-side check:

```rust
// adapter.rs lines 541-553
let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
let chunk = shard_state_header.take_chunk();
let state_root = *chunk.take_header().take_inner().prev_state_root();
if matches!(
    self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part),
    StatePartValidationResult::Invalid
) {
    return Err(Error::Other(...));
}
```

The client always validates against the header's state root. Because the server generates parts from a different root, every part is rejected.

---

### Impact Explanation

Any node performing peer-to-peer state sync at a resharding epoch boundary (sync_hash = first block of the new epoch) cannot complete state sync for shards whose `shard_index` shifted in the new layout. The serving node either returns `Error::InvalidShardId` (index out of bounds) or returns parts for the wrong trie root that the client unconditionally rejects. The node retries indefinitely and never advances past state sync, making it unable to participate in the network. This is a deterministic, permanent liveness failure for all nodes catching up at a resharding epoch.

---

### Likelihood Explanation

The bug is triggered by the combination of:
1. A resharding epoch boundary (ShardLayoutV2 or V3 in use)
2. A shard whose `shard_index` differs between the old and new epoch layouts (any shard positioned after the split point)
3. A node requesting state parts via P2P (the `StateRequestActor` path)

Condition 2 is guaranteed for every shard positioned after the split point in `ShardLayoutV2::derive` (the `splice` call inserts two new shards, shifting all subsequent shards by one index). The `StateSyncDumpProgress::Skipped` variant only skips the external-storage dump; the P2P serving path (`get_state_response_part`) is unaffected. Any unprivileged peer can send a `StateRequestPart` message with a valid `sync_hash` and an affected `shard_id`, triggering the mismatch on the serving node.

---

### Recommendation

In `get_state_response_part`, resolve `shard_index` against the **previous epoch's** shard layout, mirroring `compute_state_response_header`:

```rust
// get_state_response_part fix
let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;   // keep for shard_ids check
let shard_ids = self.epoch_manager.shard_ids(epoch_id)?;
if !shard_ids.contains(&shard_id) {
    return Err(shard_id_out_of_bounds(shard_id));
}
let prev_block = self.chain_store.get_block(header.prev_hash())?;
let prev_epoch_id = prev_block.header().epoch_id();                   // ← add
let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?; // ← add
let shard_index = prev_shard_layout.get_shard_index(shard_id)?;      // ← use prev layout
let state_root = prev_block
    .chunks()
    .get(shard_index)
    .ok_or(Error::InvalidShardId(shard_id))?
    .prev_state_root();
```

This matches the logic already used in `compute_state_response_header` and ensures both paths read the same chunk from the previous block.

---

### Proof of Concept

**Invariant violated:** `compute_state_response_header` and `get_state_response_part` must read the same `prev_state_root` for a given `(shard_id, sync_hash)` pair.

**Trigger:**
1. Deploy a ShardLayoutV2 resharding (e.g., split shard B into X and Y, shifting shard C from index 2 to index 3).
2. Bring up a node that needs to state-sync at the first block of the new epoch (sync_hash = first block of new epoch).
3. The node requests the state header for shard C → `compute_state_response_header` returns `state_root_C` (correct, from old index 2).
4. The node requests state part 0 for shard C → `get_state_response_part` uses new index 3 → `prev_block.chunks().get(3)` = `None` → returns `Error::InvalidShardId`.
5. Alternatively, for shard B (shifted from index 1 to 2): the server returns parts for shard C's trie root; `validate_state_part` on the client returns `Invalid` for every part.
6. State sync never completes for shard C or B.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** chain/chain/src/state_sync/adapter.rs (L541-553)
```rust
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
```

**File:** core/primitives/src/state_sync.rs (L240-242)
```rust
    pub fn num_state_parts(&self) -> u64 {
        get_num_state_parts(self.state_root_node().memory_usage)
    }
```

**File:** core/primitives/src/shard_layout/v2.rs (L232-263)
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
    }
```

### Title
State-Sync Part Serving Uses Wrong Epoch's Shard Layout to Index `prev_block.chunks()` at Resharding Boundaries — (File: `chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`get_state_response_part` derives `shard_index` from the **sync block's epoch** shard layout, then uses that index to read `prev_state_root` from `prev_block.chunks()`, which belongs to the **previous epoch**. At resharding boundaries (ShardLayoutV2/V3), existing shards' positions in the chunk array shift when a sibling shard is split. The resulting `state_root` is taken from the wrong chunk, breaking the root-binding invariant between the `ShardStateSyncResponseHeader` (which correctly uses the previous epoch's layout) and the state parts served by `get_state_response_part`.

---

### Finding Description

**`compute_state_response_header`** (the header path) correctly resolves the previous epoch's layout:

```rust
// chain/chain/src/state_sync/adapter.rs  lines 93-96
let shard_layout     = self.epoch_manager.get_shard_layout(sync_block_epoch_id)?;
let prev_epoch_id    = sync_prev_block.header().epoch_id();
let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
let prev_shard_index  = prev_shard_layout.get_shard_index(shard_id)?;   // ← old layout
``` [1](#0-0) 

It then reads `chunk_header.prev_state_root()` using `prev_shard_index` (old epoch's position), producing the correct `state_root_node` and embedding it in the cached `ShardStateSyncResponseHeader`.

**`get_state_response_part`** (the part-serving path) does **not** do this:

```rust
// chain/chain/src/state_sync/adapter.rs  lines 305-316
let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;   // ← NEW epoch
...
let prev_block   = self.chain_store.get_block(header.prev_hash())?;  // ← OLD epoch block
let shard_index  = shard_layout.get_shard_index(shard_id)?;          // ← NEW epoch index
let state_root   = prev_block
    .chunks()
    .get(shard_index)                                                  // ← wrong slot
    .ok_or(Error::InvalidShardId(shard_id))?
    .prev_state_root();
``` [2](#0-1) 

**Why indices diverge at resharding.** In `ShardLayoutV3::derive_impl`, splitting shard `X` at position `i` splices two new shard IDs into `shard_ids` at that position, shifting every subsequent shard's index by +1:

```rust
// core/primitives/src/shard_layout/v3.rs  lines 275-279
let [last_split] = shard_ids
    .splice(new_boundary_idx..new_boundary_idx + 1, new_shards.clone())
    .collect_array()...;
``` [3](#0-2) 

Concrete example — split old shard 1 (index 1) into new shards 4 and 5:

| Shard | Old index | New index |
|-------|-----------|-----------|
| 0     | 0         | 0         |
| 1     | 1         | (removed) |
| 4     | —         | 1         |
| 5     | —         | 2         |
| **2** | **2**     | **3** ← shifted |
| **3** | **3**     | **4** ← shifted |

For shard 2 at the resharding sync boundary:

- `compute_state_response_header` uses old index 2 → reads old shard 2's `prev_state_root` → embeds it in the header.
- `get_state_response_part` uses new index 3 → reads old shard **3**'s `prev_state_root` → generates parts against the **wrong** trie root.

The wrong `state_root` is then passed to `obtain_state_part`:

```rust
// chain/chain/src/state_sync/adapter.rs  lines 328-336
let state_part = self
    .runtime_adapter
    .obtain_state_part(
        shard_id,
        &prev_prev_hash,
        &state_root,          // ← wrong root (shard 3's, not shard 2's)
        PartId::new(part_id, num_parts),
    )
    .log_storage_error("obtain_state_part fail")?;
``` [4](#0-3) 

When the syncing node calls `set_state_part`, it validates the received part against the **header**'s `state_root` (shard 2's correct root):

```rust
// chain/chain/src/state_sync/adapter.rs  lines 541-553
let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
let chunk = shard_state_header.take_chunk();
let state_root = *chunk.take_header().take_inner().prev_state_root();
if matches!(
    self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part),
    StatePartValidationResult::Invalid
) { ... return Err(...) }
``` [5](#0-4) 

Validation fails because the part was generated against shard 3's root. Every retry produces the same wrong part; state sync for shard 2 (and shard 3) cannot complete.

---

### Impact Explanation

Any node that must state-sync at a resharding epoch boundary (e.g., a node that was offline during the resharding epoch) will be permanently unable to complete state sync for every shard whose position in the chunk array shifted. Because state sync is the only path for such nodes to rejoin the network, they are effectively locked out. This is a **High** severity liveness failure: affected nodes cannot participate in consensus or serve RPC after a resharding event.

---

### Likelihood Explanation

Resharding (V2/V3 shard layout transitions) is a planned protocol upgrade path in nearcore. Any node that is offline during a resharding epoch and then attempts to state-sync will hit this path. The condition is deterministic and reproducible whenever a shard split shifts the indices of surviving shards.

---

### Recommendation

In `get_state_response_part`, mirror the pattern used in `compute_state_response_header`: resolve the **previous epoch's** shard layout and use `prev_shard_layout.get_shard_index(shard_id)` to index into `prev_block.chunks()`. Specifically:

```rust
let prev_block = self.chain_store.get_block(header.prev_hash())?;
let prev_epoch_id = prev_block.header().epoch_id();
let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
let shard_index = prev_shard_layout.get_shard_index(shard_id)?;
let state_root = prev_block
    .chunks()
    .get(shard_index)
    .ok_or(Error::InvalidShardId(shard_id))?
    .prev_state_root();
```

This aligns the state root used for part generation with the state root committed in the header.

---

### Proof of Concept

1. Configure a network with `ShardLayoutV3` (or V2) and at least 4 shards.
2. Trigger a shard split (e.g., split shard at index 1 into two new shards), advancing to the next epoch.
3. Take a node offline before the resharding epoch begins; restart it after the resharding epoch has finalized.
4. The node enters state sync for the resharding epoch's sync hash.
5. Observe that `get_state_response_header` succeeds for shard 2 (old index 2, new index 3).
6. Observe that `get_state_response_part` for shard 2 calls `obtain_state_part` with old shard 3's `prev_state_root` (extracted via new index 3 from `prev_block.chunks()`).
7. `set_state_part` rejects every part with `validate_state_part failed`; state sync loops indefinitely.

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L93-96)
```rust
        let shard_layout = self.epoch_manager.get_shard_layout(sync_block_epoch_id)?;
        let prev_epoch_id = sync_prev_block.header().epoch_id();
        let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
        let prev_shard_index = prev_shard_layout.get_shard_index(shard_id)?;
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

**File:** chain/chain/src/state_sync/adapter.rs (L328-336)
```rust
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

**File:** core/primitives/src/shard_layout/v3.rs (L275-279)
```rust
        let [last_split] = shard_ids
            .splice(new_boundary_idx..new_boundary_idx + 1, new_shards.clone())
            .collect_array()
            .expect("should only splice one shard");
        shards_split_map.insert(last_split, new_shards);
```

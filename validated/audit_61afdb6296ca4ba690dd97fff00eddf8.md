### Title
`get_state_response_part` Uses New-Epoch Shard Layout to Index Old-Epoch Chunks, Producing Wrong `state_root` and `num_parts` at Resharding Boundaries — (`chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`get_state_response_part` resolves `shard_index` from the **new epoch's** `ShardLayout` (the epoch of `sync_hash`) but immediately uses that index to subscript `prev_block.chunks()`, which belongs to the **old epoch**. The companion function `compute_state_response_header` correctly uses the previous epoch's layout for the same indexing step. The mismatch causes the wrong `state_root` to be read, which in turn produces a wrong `num_parts` value. Because the header and the part server now disagree on `num_parts`, every part the server generates carries an incorrect `PartId::total`, and the receiving node's `validate_state_part` call will reject all of them.

---

### Finding Description

**`compute_state_response_header`** (the correct path):

```rust
// chain/chain/src/state_sync/adapter.rs  lines 93-96
let shard_layout = self.epoch_manager.get_shard_layout(sync_block_epoch_id)?; // NEW epoch
let prev_epoch_id = sync_prev_block.header().epoch_id();
let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?; // OLD epoch
let prev_shard_index = prev_shard_layout.get_shard_index(shard_id)?;          // ← OLD layout
```

`prev_shard_index` is then used to subscript `sync_prev_block.chunks()` (the old epoch's block). This is correct: the old epoch's chunk array is indexed by the old epoch's layout.

**`get_state_response_part`** (the buggy path):

```rust
// chain/chain/src/state_sync/adapter.rs  lines 295, 305, 310-316
let epoch_id = block.header().epoch_id();                          // NEW epoch
let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?; // NEW epoch layout
let prev_block = self.chain_store.get_block(header.prev_hash())?;  // OLD epoch's last block
let shard_index = shard_layout.get_shard_index(shard_id)?;         // ← NEW layout index
let state_root = prev_block
    .chunks()
    .get(shard_index)          // ← OLD epoch's chunk array, indexed by NEW layout
    .ok_or(Error::InvalidShardId(shard_id))?
    .prev_state_root();
```

`shard_index` comes from the **new** epoch's layout but is applied to `prev_block.chunks()`, which is ordered by the **old** epoch's layout. This is the same structural error as the AJNA M-5 bug: one boundary value (LUP) was computed with old state while the other (HTP) was computed with new state, producing a wrong accrual index. Here, the header uses the old layout's index while the part server uses the new layout's index, producing a wrong `state_root`.

The wrong `state_root` then propagates into `num_parts`:

```rust
// lines 319-323
let state_root_node = self.runtime_adapter
    .get_state_root_node(shard_id, &prev_hash, &state_root)  // wrong state_root
    .log_storage_error("get_state_root_node fail")?;
let num_parts = get_num_state_parts(state_root_node.memory_usage); // wrong num_parts
```

The header's `num_state_parts()` is derived from the correct `state_root_node` (via `compute_state_response_header`). The part server's `num_parts` is derived from a different (wrong) `state_root_node`. The `PartId { idx, total }` embedded in every generated part therefore has a different `total` than what the header advertises, and `Trie::validate_state_part` will reject every part.

**Concrete resharding scenario:**

Suppose epoch N has shards `[S0, S1, S2, S3]` (indices 0–3) and epoch N+1 splits S2 into S2a and S2b, giving `[S0, S1, S2a, S2b, S3]` (indices 0–4). The sync_hash is the first block of epoch N+1; `prev_block` is the last block of epoch N (4 chunks, indices 0–3).

| shard_id | new-layout index | `prev_block.chunks().get(idx)` | result |
|---|---|---|---|
| S0 | 0 | chunk for S0 | correct (index unchanged) |
| S1 | 1 | chunk for S1 | correct |
| S2a | 2 | chunk for S2 (parent) | **wrong state_root** (parent, not child) |
| S2b | 3 | chunk for S3 | **wrong state_root** (sibling shard) |
| S3 | 4 | `None` | `Error::InvalidShardId` | [1](#0-0) [2](#0-1) 

---

### Impact Explanation

At a resharding epoch boundary any node that serves state parts via `get_state_response_part` (the peer-to-peer path, called from `StateRequestActor`) will:

1. Return `Error::InvalidShardId` for child shards whose new-layout index exceeds the old chunk count.
2. Return a state part whose `PartId::total` disagrees with the header's `num_state_parts()` for shards whose index shifted.

A syncing node that receives these parts will fail `validate_state_part` for every part, making state sync impossible at resharding boundaries via the peer-to-peer path. The external dump path already marks resharding epochs as `Skipped`, but the peer-to-peer path has no such guard. [3](#0-2) [4](#0-3) 

---

### Likelihood Explanation

The bug is latent until a resharding epoch boundary is reached. Resharding is a planned, protocol-level event. Once it occurs, every peer-to-peer state sync request for a shard whose index shifted (or for a new child shard) will hit the bug. The `sync_hash` is supplied by the requesting peer; no special privilege is required to trigger the code path. [5](#0-4) 

---

### Recommendation

In `get_state_response_part`, resolve `shard_index` from the **previous epoch's** shard layout, exactly as `compute_state_response_header` does:

```rust
// After obtaining prev_block:
let prev_epoch_id = prev_block.header().epoch_id();
let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
let shard_index = prev_shard_layout.get_shard_index(shard_id)?;
```

This makes the `state_root` and `num_parts` computed in `get_state_response_part` consistent with those embedded in the header produced by `compute_state_response_header`. [6](#0-5) 

---

### Proof of Concept

1. Run a two-node testnet with epoch length 8 and a resharding scheduled at epoch 2 (e.g., using `TestLoopBuilder` with a static shard layout change).
2. Let node 0 advance past the resharding boundary.
3. From node 1, call `get_state_response_header(child_shard_id, sync_hash)` — this succeeds and returns `num_state_parts = H`.
4. Call `get_state_response_part(child_shard_id, 0, sync_hash)` on node 0 — this either returns `Error::InvalidShardId` (if the new-layout index ≥ old chunk count) or returns a part whose embedded `PartId::total ≠ H`.
5. Pass the part to `set_state_part` on node 1 — `validate_state_part` rejects it because `state_root` or `PartId::total` is wrong.

The discrepancy between `compute_state_response_header` (uses `prev_shard_layout`) and `get_state_response_part` (uses new-epoch `shard_layout`) is the direct root cause, mirroring the AJNA M-5 pattern of mixing old-state and new-state values in a boundary computation. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L63-96)
```rust
    /// Computes ShardStateSyncResponseHeader.
    pub fn compute_state_response_header(
        &self,
        shard_id: ShardId,
        sync_hash: CryptoHash,
    ) -> Result<ShardStateSyncResponseHeader, Error> {
        // Consistency rules:
        // 1. Everything prefixed with `sync_` indicates new epoch, for which we are syncing.
        // 1a. `sync_prev` means the last of the prev epoch.
        // 2. Empty prefix means the height where chunk was applied last time in the prev epoch.
        //    Let's call it `current`.
        // 2a. `prev_` means we're working with height before current.
        // 3. In inner loops we use all prefixes with no relation to the context described above.
        let sync_block = self
            .chain_store
            .get_block(&sync_hash)
            .log_storage_error("block has already been checked for existence")?;
        let sync_block_header = sync_block.header();
        let sync_block_epoch_id = sync_block_header.epoch_id();
        let shard_ids = self.epoch_manager.shard_ids(sync_block_epoch_id)?;
        if !shard_ids.contains(&shard_id) {
            return Err(shard_id_out_of_bounds(shard_id));
        }

        // The chunk was applied at height `chunk_header.height_included`.
        // Getting the `current` state.
        // TODO(current_epoch_state_sync): check that the sync block is what we would expect. So, either the first
        // block of an epoch, or the first block where there have been two new chunks in the epoch
        let sync_prev_block = self.chain_store.get_block(sync_block_header.prev_hash())?;

        let shard_layout = self.epoch_manager.get_shard_layout(sync_block_epoch_id)?;
        let prev_epoch_id = sync_prev_block.header().epoch_id();
        let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
        let prev_shard_index = prev_shard_layout.get_shard_index(shard_id)?;
```

**File:** chain/chain/src/state_sync/adapter.rs (L277-336)
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
```

**File:** core/primitives/src/state_sync.rs (L240-242)
```rust
    pub fn num_state_parts(&self) -> u64 {
        get_num_state_parts(self.state_root_node().memory_usage)
    }
```

**File:** core/primitives/src/state_sync.rs (L351-355)
```rust
pub const STATE_PART_MEMORY_LIMIT: bytesize::ByteSize = bytesize::ByteSize(30 * bytesize::MIB);

pub fn get_num_state_parts(memory_usage: u64) -> u64 {
    (memory_usage + STATE_PART_MEMORY_LIMIT.as_u64() - 1) / STATE_PART_MEMORY_LIMIT.as_u64()
}
```

**File:** chain/chain/src/runtime/mod.rs (L531-551)
```rust
    fn validate_state_part_impl(
        &self,
        state_root: &StateRoot,
        part_id: PartId,
        part: &StatePart,
    ) -> StatePartValidationResult {
        let partial_state = part.to_partial_state();
        let Ok(partial_state) = part.to_partial_state() else {
            // Deserialization error means we've got the data from malicious peer
            tracing::error!(target: "state-parts", ?partial_state, "state part deserialization error");
            return StatePartValidationResult::Invalid;
        };
        match Trie::validate_state_part(state_root, part_id, partial_state) {
            Ok(_) => StatePartValidationResult::Valid,
            // Storage error should not happen
            Err(err) => {
                tracing::error!(target: "state-parts", ?err, "state part storage error");
                StatePartValidationResult::Invalid
            }
        }
    }
```

**File:** chain/client/src/state_request_actor.rs (L260-293)
```rust
impl Handler<StateRequestPart, Option<StatePartOrHeader>> for StateRequestActor {
    fn handle(&mut self, msg: StateRequestPart) -> Option<StatePartOrHeader> {
        let StateRequestPart { shard_id, sync_hash, part_id } = msg;
        let _timer =
            metrics::STATE_SYNC_REQUEST_TIME.with_label_values(&["StateRequestPart"]).start_timer();
        let _span =
            tracing::debug_span!(target: "sync", "StateRequestPart", ?shard_id, ?sync_hash, part_id)
                .entered();

        tracing::debug!(target: "sync", "handle state request part");

        if self.throttle_state_sync_request() {
            metrics::STATE_SYNC_REQUESTS_THROTTLED_TOTAL.inc();
            return None;
        }

        if self.validate_sync_hash(&sync_hash) == SyncHashValidationResult::Rejected {
            metrics::STATE_SYNC_REQUESTS_SERVED_TOTAL.with_label_values(&["part", "failed"]).inc();
            return None;
        }

        tracing::debug!(target: "sync", "computing state request part");
        let part = self.state_sync_adapter.get_state_response_part(shard_id, part_id, sync_hash);
        let Ok(part) = part else {
            tracing::warn!(target: "sync", ?part, "cannot build state part");
            metrics::STATE_SYNC_REQUESTS_SERVED_TOTAL.with_label_values(&["part", "failed"]).inc();
            return Some(new_part_response_empty(shard_id, sync_hash));
        };
        tracing::trace!(target: "sync", "finished computation for state request part");

        metrics::STATE_SYNC_REQUESTS_SERVED_TOTAL.with_label_values(&["part", "success"]).inc();
        let response = new_part_response(shard_id, sync_hash, part_id, Some(part));
        Some(response)
    }
```

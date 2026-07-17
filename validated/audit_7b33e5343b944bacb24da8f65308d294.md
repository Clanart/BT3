### Title
Child `ChunkExtra.proposed_split` Inherits Parent's Non-None Value After Resharding, Causing First Child Chunk to Fail Validation — (`File: chain/chain/src/resharding/manager.rs`)

---

### Summary

When `process_memtrie_resharding_storage_update` creates the child shard's `ChunkExtra` by cloning the parent's, it only updates `state_root` and `congestion_info`. It does **not** reset `proposed_split` to `None`. The parent's `ChunkExtra` at the resharding boundary block carries `proposed_split = Some(TrieSplit{...})` — the very proposal that triggered the split. The child inherits this stale value. The first chunk produced for the child shard in epoch N+2 computes `proposed_split = None` (child is new, below threshold), so its header carries `None`. `validate_chunk_with_chunk_extra_and_receipts_root` then compares `prev_chunk_extra.proposed_split() = Some(TrieSplit{...})` against `chunk_header.proposed_split() = None`, finds a mismatch, and returns `InvalidChunkHeaderShardSplit`, rejecting every first chunk of every child shard after a dynamic resharding.

---

### Finding Description

**Overwrite site** — `chain/chain/src/resharding/manager.rs`, `process_memtrie_resharding_storage_update`:

```rust
// TODO(resharding): set all fields of `ChunkExtra`. Consider stronger
// typing. Clarify where it should happen when `State` and
// `FlatState` update is implemented.
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
// proposed_split is NOT reset — it is inherited from parent
``` [1](#0-0) 

The parent `ChunkExtra` at the resharding boundary block is `ChunkExtraV5` with `proposed_split = Some(TrieSplit { boundary_account, left_memory, right_memory })` — the proposal that caused the split. The clone carries this value into the child's `ChunkExtra`.

**Validation site** — `chain/chain/src/validate.rs`, `validate_chunk_with_chunk_extra_and_receipts_root`:

```rust
if prev_chunk_extra.proposed_split() != chunk_header.proposed_split() {
    DYNAMIC_RESHARDING_VALIDATION_FAILURES.with_label_values(&["chunk_header"]).inc();
    return Err(Error::InvalidChunkHeaderShardSplit(...));
}
``` [2](#0-1) 

The first chunk produced for the child shard in epoch N+2 runs `compute_proposed_split`, which returns `None` (child is freshly created, well below the memory threshold). The chunk header therefore carries `proposed_split = None`. The stored child `ChunkExtra` carries `proposed_split = Some(TrieSplit{...})`. The comparison fails.

**`ChunkExtraV5.proposed_split` field** — the field that is not reset: [3](#0-2) 

The codebase's own architecture document acknowledges this at TODO item 10:

> `chain/chain/src/resharding/manager.rs:249` — The resharding manager doesn't set all `ChunkExtra` fields (notably the new `proposed_split` field).



---

### Impact Explanation

Every child shard produced by a dynamic resharding has its first chunk rejected with `InvalidChunkHeaderShardSplit`. This breaks the resharding process entirely: child shards cannot produce accepted chunks in epoch N+2, stalling the chain for all shards that were split. The bug only causes valid chunks to be rejected; it does not allow invalid state to be accepted.

---

### Likelihood Explanation

The path is reached automatically whenever `ProtocolFeature::DynamicResharding` is enabled and a shard split executes. No privileged action beyond the normal protocol is required. The resharding is triggered deterministically by trie memory thresholds or `force_split_shards` config. Every dynamic resharding event hits this code path for both child shards.

---

### Recommendation

After cloning the parent `ChunkExtra`, explicitly reset `proposed_split` to `None` for the child:

```rust
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
*child_chunk_extra.proposed_split_mut() = None;  // child is new; no split proposed yet
```

This mirrors the fix described in the external report: do not carry over the parent's commitment-binding field into the child record when the child's first chunk will compute a different (default) value.

---

### Proof of Concept

1. Enable `ProtocolFeature::DynamicResharding` (protocol version ≥ 153 with dynamic config).
2. Fill a shard past `memory_usage_threshold` so `compute_proposed_split` returns `Some(TrieSplit{...})` at the epoch boundary block.
3. The epoch boundary block's parent `ChunkExtra` has `proposed_split = Some(TrieSplit{...})`.
4. `process_memtrie_resharding_storage_update` clones this into both child `ChunkExtra` records and saves them under the child `ShardUId` keys.
5. In epoch N+2, the chunk producer for the left child shard calls `compute_proposed_split` → returns `None` (child is new, below threshold). The produced chunk header has `proposed_split = None`.
6. `validate_chunk_with_chunk_extra_and_receipts_root` loads the child's `ChunkExtra` (with `proposed_split = Some(...)`), compares against the header's `None`, and returns `Err(InvalidChunkHeaderShardSplit(...))`.
7. The first chunk of both child shards is rejected; resharding is broken. [4](#0-3) [2](#0-1)

### Citations

**File:** chain/chain/src/resharding/manager.rs (L255-266)
```rust
            // TODO(resharding): set all fields of `ChunkExtra`. Consider stronger
            // typing. Clarify where it should happen when `State` and
            // `FlatState` update is implemented.
            let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
            *child_chunk_extra.state_root_mut() = trie_changes.new_root;
            *child_chunk_extra.congestion_info_mut() = child_congestion_info;

            chain_store_update.save_chunk_extra(
                block_hash,
                &new_shard_uid,
                child_chunk_extra.into(),
            );
```

**File:** chain/chain/src/validate.rs (L176-185)
```rust
    if prev_chunk_extra.proposed_split() != chunk_header.proposed_split() {
        DYNAMIC_RESHARDING_VALIDATION_FAILURES.with_label_values(&["chunk_header"]).inc();
        return Err(Error::InvalidChunkHeaderShardSplit(format!(
            "header has {:?}, expected {:?} (prev block hash: {:?} height created: {:?})",
            chunk_header.proposed_split(),
            prev_chunk_extra.proposed_split(),
            chunk_header.prev_block_hash(),
            chunk_header.height_created(),
        )));
    }
```

**File:** core/primitives/src/types.rs (L898-900)
```rust
        /// Proposed split of this shard (dynamic resharding).
        pub proposed_split: Option<TrieSplit>,
    }
```

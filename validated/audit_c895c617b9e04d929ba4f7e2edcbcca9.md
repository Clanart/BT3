### Title
Uninitialized `proposed_split` Field in Child `ChunkExtra` After Resharding Causes Permanent Chunk Validation Failure - (File: `chain/chain/src/resharding/manager.rs`)

### Summary

During a shard split, the child shard's `ChunkExtra` is created by cloning the parent's `ChunkExtra` without resetting the `proposed_split` field to `None`. The parent's `ChunkExtra` carries `proposed_split = Some(TrieSplit{...})` (the very value that triggered the split). The child shard's first chunk header, produced independently by the chunk producer via `compute_proposed_split()`, will always compute `proposed_split = None` (the child is newly created and below the memory threshold). The mismatch between the stored `ChunkExtra.proposed_split` and the chunk header's `proposed_split` causes `validate_chunk_with_chunk_extra_and_receipts_root` to return `InvalidChunkHeaderShardSplit`, permanently blocking the child shard from producing valid chunks.

### Finding Description

In `process_memtrie_resharding_storage_update`, the child `ChunkExtra` is constructed as:

```rust
// chain/chain/src/resharding/manager.rs:258-260
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
```

Only `state_root` and `congestion_info` are updated. The `proposed_split` field — which exists in `ChunkExtraV5` — is silently inherited from the parent:

```rust
// core/primitives/src/types.rs:898-899
/// Proposed split of this shard (dynamic resharding).
pub proposed_split: Option<TrieSplit>,
```

The parent's `ChunkExtra` has `proposed_split = Some(TrieSplit{boundary_account, left_mem, right_mem})` because that is what triggered the resharding. The child's stored `ChunkExtra` therefore also carries `proposed_split = Some(...)`.

When the first chunk of the child shard is produced in the new epoch, `compute_proposed_split()` runs fresh on the child's trie. The child shard is newly created and well below the memory threshold, so it returns `None`. The chunk header is signed with `proposed_split = None`.

Validation then compares:

```rust
// chain/chain/src/validate.rs:176-185
if prev_chunk_extra.proposed_split() != chunk_header.proposed_split() {
    return Err(Error::InvalidChunkHeaderShardSplit(format!(
        "header has {:?}, expected {:?} ...",
        chunk_header.proposed_split(),
        prev_chunk_extra.proposed_split(),
    )));
}
```

`prev_chunk_extra.proposed_split()` = `Some(TrieSplit{...})` (inherited from parent)
`chunk_header.proposed_split()` = `None` (freshly computed)

Every validator rejects the chunk. Since no valid chunk can be produced for the child shard, it is permanently frozen.

The codebase itself acknowledges the root cause with an explicit TODO:

```rust
// chain/chain/src/resharding/manager.rs:255-257
// TODO(resharding): set all fields of `ChunkExtra`. Consider stronger
// typing. Clarify where it should happen when `State` and
// `FlatState` update is implemented.
```

And the architecture documentation confirms the field is not set:

> `chain/chain/src/resharding/manager.rs:249` — The resharding manager doesn't set all `ChunkExtra` fields (notably the new `proposed_split` field).

### Impact Explanation

When `ProtocolFeature::DynamicResharding` is enabled and a shard split occurs, every child shard's `ChunkExtra` at the resharding block carries the parent's `proposed_split = Some(TrieSplit{...})`. No chunk producer can produce a valid first chunk for the child shard because the freshly computed `proposed_split = None` will always mismatch the stored value. All validators reject the chunk with `InvalidChunkHeaderShardSplit`. The child shard is permanently frozen — no transactions or receipts are processed — until the node is patched and the stored `ChunkExtra` is corrected. This constitutes a **Critical** protocol-level chain halt scoped to all child shards created by dynamic resharding.

### Likelihood Explanation

The bug is deterministically triggered on every dynamic resharding event. It requires no adversarial input: the resharding is initiated automatically by the protocol when a shard's trie memory usage exceeds `memory_usage_threshold`. Any user activity that grows a shard's state past the threshold (contract deployments, large data writes) indirectly triggers the condition. The bug is latent until `ProtocolFeature::DynamicResharding` is enabled in production.

### Recommendation

In `process_memtrie_resharding_storage_update`, after cloning the parent `ChunkExtra`, explicitly reset `proposed_split` to `None` for each child shard:

```rust
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
// Reset proposed_split: the child shard has no split proposal yet.
// Inheriting the parent's value causes InvalidChunkHeaderShardSplit on the
// child's first chunk, because compute_proposed_split() returns None for a
// newly created shard that is below the memory threshold.
if let Some(v5) = child_chunk_extra.as_v5_mut() {
    v5.proposed_split = None;
}
```

Alternatively, add a `proposed_split_mut()` accessor to `ChunkExtra` (analogous to `state_root_mut()`) and use it here. The broader TODO to "set all fields of `ChunkExtra`" should be resolved before enabling `DynamicResharding` in production.

### Proof of Concept

1. Enable `ProtocolFeature::DynamicResharding` and configure `DynamicReshardingConfig` with a low `memory_usage_threshold`.
2. Run a node and grow a shard's state past the threshold (e.g., deploy large contracts).
3. Observe the resharding block: `process_memtrie_resharding_storage_update` creates child `ChunkExtra` entries by cloning the parent. Dump the stored `ChunkExtra` for a child shard and confirm `proposed_split = Some(TrieSplit{...})`.
4. In the next epoch, observe the chunk producer for the child shard compute `proposed_split = None` via `compute_proposed_split()` and embed it in the chunk header.
5. Observe `validate_chunk_with_chunk_extra_and_receipts_root` return `InvalidChunkHeaderShardSplit` on every validator, with `expected Some(TrieSplit{...}), got None`.
6. Confirm the child shard produces no new chunks and is permanently frozen.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** chain/chain/src/resharding/manager.rs (L255-260)
```rust
            // TODO(resharding): set all fields of `ChunkExtra`. Consider stronger
            // typing. Clarify where it should happen when `State` and
            // `FlatState` update is implemented.
            let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
            *child_chunk_extra.state_root_mut() = trie_changes.new_root;
            *child_chunk_extra.congestion_info_mut() = child_congestion_info;
```

**File:** core/primitives/src/types.rs (L898-900)
```rust
        /// Proposed split of this shard (dynamic resharding).
        pub proposed_split: Option<TrieSplit>,
    }
```

**File:** core/primitives/src/types.rs (L1066-1071)
```rust
        pub fn proposed_split(&self) -> Option<&TrieSplit> {
            match self {
                Self::V1(_) | Self::V2(_) | Self::V3(_) | Self::V4(_) => None,
                ChunkExtra::V5(v5) => v5.proposed_split.as_ref(),
            }
        }
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

**File:** chain/chain/src/runtime/mod.rs (L591-593)
```rust
        if !ProtocolFeature::DynamicResharding.enabled(protocol_version) {
            return Ok(None);
        }
```

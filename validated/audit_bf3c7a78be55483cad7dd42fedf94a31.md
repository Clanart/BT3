### Title
Child `ChunkExtra.proposed_split` Inherits Parent's Non-None Value After Shard Split, Breaking First-Chunk Validation — (`chain/chain/src/resharding/manager.rs`)

### Summary

During dynamic resharding, `process_memtrie_resharding_storage_update()` creates each child shard's `ChunkExtra` by cloning the parent's `ChunkExtra` and updating only `state_root` and `congestion_info`. The `proposed_split` field — which holds the `TrieSplit` value that *triggered* the resharding — is never reset to `None`. When the child shard's first chunk is produced in epoch N+2, `compute_proposed_split` correctly returns `None` (the resharding cooldown blocks a new proposal), but `validate_chunk_with_chunk_extra_and_receipts_root` compares that `None` against the stored `prev_chunk_extra.proposed_split()` which is still `Some(TrieSplit{…})`. The mismatch produces `InvalidChunkHeaderShardSplit`, permanently blocking the child shard from accepting any chunk.

### Finding Description

**Root cause — uncleared field in child `ChunkExtra`:** [1](#0-0) 

```rust
// TODO(resharding): set all fields of `ChunkExtra`. ...
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;

chain_store_update.save_chunk_extra(block_hash, &new_shard_uid, child_chunk_extra.into());
```

The parent's `ChunkExtra` at the resharding boundary block carries `proposed_split = Some(TrieSplit{boundary_account, left_mem, right_mem})` — the exact value that caused the split to be scheduled. The clone propagates this value verbatim into both child `ChunkExtra` records. Only `state_root` and `congestion_info` are overwritten; `proposed_split` is silently inherited.

**The `proposed_split` field in `ChunkExtraV5`:** [2](#0-1) 

**Validation that fires on the child shard's first chunk:** [3](#0-2) 

```rust
if prev_chunk_extra.proposed_split() != chunk_header.proposed_split() {
    DYNAMIC_RESHARDING_VALIDATION_FAILURES.with_label_values(&["chunk_header"]).inc();
    return Err(Error::InvalidChunkHeaderShardSplit(...));
}
```

**Why the child shard's first chunk always produces `None`:** [4](#0-3) 

`compute_proposed_split` returns `None` when `can_reshard` is false. After a resharding at epoch N, `last_resharding = N`; in epoch N+2 the cooldown (`epoch_height − last_resharding < min_epochs_between_resharding`) blocks any new proposal, so the chunk header carries `proposed_split = None`.

**Validation call site in regular block processing:** [5](#0-4) 

The mismatch — `prev_chunk_extra.proposed_split() = Some(…)` vs `chunk_header.proposed_split() = None` — is caught here and returns `InvalidChunkHeaderShardSplit`, rejecting the child shard's first chunk.

**The project's own documentation acknowledges the gap:** [6](#0-5) 

> **`chain/chain/src/resharding/manager.rs:249`** — The resharding manager doesn't set all `ChunkExtra` fields (notably the new `proposed_split` field).

### Impact Explanation

When `ProtocolFeature::DynamicResharding` is active and a shard split executes, every child shard's first chunk fails `validate_chunk_with_chunk_extra_and_receipts_root` with `InvalidChunkHeaderShardSplit`. No chunk producer can advance the child shard; the shard is permanently stalled at the epoch boundary. Because the child shards together cover the full key-space of the parent, the entire resharded portion of the network halts. This is a **Critical** impact: a protocol-level liveness failure triggered automatically by the resharding mechanism itself.

### Likelihood Explanation

The trigger is deterministic and requires no adversarial action: any node that executes a dynamic resharding (shard memory exceeds `memory_usage_threshold`) will store the incorrect child `ChunkExtra`. The bug fires on the very first chunk of each child shard in epoch N+2. It is gated only by `ProtocolFeature::DynamicResharding` being enabled; once that feature is live in production the failure is guaranteed on the first resharding event.

### Recommendation

In `process_memtrie_resharding_storage_update`, after cloning the parent `ChunkExtra`, explicitly clear `proposed_split` for each child:

```rust
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
// FIX: child shards start with no pending split proposal
*child_chunk_extra.proposed_split_mut() = None;
```

Add a `proposed_split_mut()` accessor to `ChunkExtra` (mirroring the existing `state_root_mut()` pattern) and enforce via a stronger-typed builder that all fields are explicitly set rather than inherited from a clone.

### Proof of Concept

1. Enable `ProtocolFeature::DynamicResharding` (set protocol version ≥ the enabling version).
2. Configure `DynamicReshardingConfig` with a low `memory_usage_threshold` so a shard triggers a split within a few epochs.
3. Run the network through the resharding boundary (epoch N → N+2).
4. Observe that the chunk producer for each child shard emits a chunk header with `proposed_split = None`.
5. Observe that `validate_chunk_with_chunk_extra_and_receipts_root` reads `prev_chunk_extra.proposed_split() = Some(TrieSplit{…})` from the stored child `ChunkExtra` and returns `Err(InvalidChunkHeaderShardSplit)`.
6. The child shard produces no accepted chunks; the resharded portion of the network is halted.

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

**File:** core/primitives/src/types.rs (L879-900)
```rust
    /// V4 -> V5: add proposed_split (dynamic resharding)
    #[derive(Debug, PartialEq, BorshSerialize, BorshDeserialize, Clone, Eq, serde::Serialize)]
    pub struct ChunkExtraV5 {
        /// Post state root after applying give chunk.
        pub state_root: StateRoot,
        /// Root of merklizing results of receipts (transactions) execution.
        pub outcome_root: CryptoHash,
        /// Validator proposals produced by given chunk.
        pub validator_proposals: Vec<ValidatorStake>,
        /// Actually how much gas were used.
        pub gas_used: Gas,
        /// Gas limit, allows to increase or decrease limit based on expected time vs real time for computing the chunk.
        pub gas_limit: Gas,
        /// Total balance burnt after processing the current chunk.
        pub balance_burnt: Balance,
        /// Congestion info about this shard after the chunk was applied.
        congestion_info: CongestionInfo,
        /// Requests for bandwidth to send receipts to other shards.
        pub bandwidth_requests: BandwidthRequests,
        /// Proposed split of this shard (dynamic resharding).
        pub proposed_split: Option<TrieSplit>,
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

**File:** chain/chain/src/runtime/mod.rs (L591-619)
```rust
        if !ProtocolFeature::DynamicResharding.enabled(protocol_version) {
            return Ok(None);
        }

        let Some(config) = epoch_config.dynamic_resharding_config() else {
            return Ok(None);
        };

        if !self.epoch_manager.is_next_block_possibly_last_in_epoch(height, prev_block_hash)? {
            return Ok(None);
        }

        if !self.epoch_manager.can_reshard(prev_block_hash, config.min_epochs_between_resharding)? {
            return Ok(None);
        }

        let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;
        let shard_uid = ShardUId::from_shard_id_and_layout(shard_id, &shard_layout);
        match check_dynamic_resharding(shard_trie, shard_id, shard_layout, config) {
            Err(FindSplitError::Storage(err)) => Err(err)?,
            Err(err) => {
                DYNAMIC_RESHARDING_FIND_SPLIT_ERRORS
                    .with_label_values(&[&shard_uid.to_string()])
                    .inc();
                tracing::error!(target: "runtime", ?shard_id, ?err, "dynamic resharding check failed");
                Ok(None)
            }
            Ok(split) => Ok(split),
        }
```

**File:** chain/chain/src/chain.rs (L3454-3477)
```rust
            // Validate that all next chunk information matches previous chunk extra.
            validate_chunk_with_chunk_extra(
                // It's safe here to use ChainStore instead of ChainStoreUpdate
                // because we're asking prev_chunk_header for already committed block
                self.chain_store(),
                self.epoch_manager.as_ref(),
                prev_hash,
                prev_chunk_extra.as_ref(),
                prev_chunk_height_included,
                chunk_header,
            )
            .map_err(|err| {
                tracing::warn!(
                    target: "chain",
                    ?err,
                    %shard_id,
                    prev_chunk_height_included,
                    ?prev_chunk_extra,
                    ?chunk_header,
                    "failed to validate chunk extra"
                );
                byzantine_assert!(false);
                err
            })?;
```

**File:** docs/architecture/how/dynamic_resharding.md (L282-282)
```markdown
10. **`chain/chain/src/resharding/manager.rs:249`** -- The resharding manager doesn't set all `ChunkExtra` fields (notably the new `proposed_split` field).
```

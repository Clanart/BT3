### Title
Child `ChunkExtra.proposed_split` Inherits Stale Parent Value After Shard Split, Causing Permanent `InvalidChunkHeaderShardSplit` for Child Shard — (`chain/chain/src/resharding/manager.rs`)

### Summary

During dynamic resharding, when a parent shard is split into child shards, the child `ChunkExtra` is created by cloning the parent's `ChunkExtra` but only updating `state_root` and `congestion_info`. The `proposed_split` field is **not reset to `None`**, so the child shard inherits the parent's stale split proposal. The child shard's first chunk in the new epoch is produced with `proposed_split = None` (correctly computed), but validation compares it against the stale `Some(TrieSplit{...})` in the child `ChunkExtra`, triggering a permanent `InvalidChunkHeaderShardSplit` rejection that halts the child shard indefinitely.

### Finding Description

In `process_memtrie_resharding_storage_update`, the child `ChunkExtra` is built by cloning the parent and only patching two fields:

```rust
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
// proposed_split is NOT reset — inherits parent's Some(TrieSplit{...})
``` [1](#0-0) 

The parent shard's final chunk (the one that triggered the resharding decision) has `proposed_split = Some(TrieSplit{boundary_account, left_memory, right_memory})` stored in its `ChunkExtra`. This value is silently inherited by both child `ChunkExtra` records.

In epoch N+2, when the child shard's first chunk is produced, `compute_proposed_split` returns `None` because `is_next_block_possibly_last_in_epoch` is false for early-epoch blocks:

```rust
if !self.epoch_manager.is_next_block_possibly_last_in_epoch(height, prev_block_hash)? {
    return Ok(None);
}
``` [2](#0-1) 

Block preprocessing then calls `validate_chunk_with_chunk_extra_and_receipts_root`, which enforces:

```rust
if prev_chunk_extra.proposed_split() != chunk_header.proposed_split() {
    return Err(Error::InvalidChunkHeaderShardSplit(...));
}
``` [3](#0-2) 

The mismatch is:
- `prev_chunk_extra.proposed_split()` = `Some(TrieSplit{...})` — stale, inherited from parent
- `chunk_header.proposed_split()` = `None` — correctly computed by the chunk producer

Every subsequent block repeats the same failure: the old-chunk path propagates the same stale `ChunkExtra` forward without ever clearing `proposed_split`, so the child shard is permanently stuck.

The codebase explicitly acknowledges this gap:

> `chain/chain/src/resharding/manager.rs:249` — The resharding manager doesn't set all `ChunkExtra` fields (notably the new `proposed_split` field). [4](#0-3) 

The `ChunkExtraV5` struct that carries `proposed_split`: [5](#0-4) 

The `ShardChunkHeaderInnerV5` field that must match: [6](#0-5) 

### Impact Explanation

The child shard is permanently unable to process new chunks after resharding. Every chunk produced for the child shard fails `InvalidChunkHeaderShardSplit` validation. The child shard is treated as having perpetually missing chunks, its state never advances past the resharding boundary, and the shard is effectively halted. This is a **Critical** chain-level impact: a resharding event that is supposed to scale the network instead permanently disables the newly created shards.

### Likelihood Explanation

This bug fires on every dynamic resharding event where the parent shard's final chunk carries a non-`None` `proposed_split`. That is the normal case: a shard is split precisely because it exceeded the memory threshold, so `compute_proposed_split` returns `Some(...)` for the boundary block. The `force_split_shards` configuration used in tests also triggers the same path. The bug is latent today because `ProtocolFeature::DynamicResharding` is not yet enabled on mainnet, but it will fire on the first production resharding.

### Recommendation

In `process_memtrie_resharding_storage_update`, explicitly clear `proposed_split` on the child `ChunkExtra` immediately after cloning:

```rust
let mut child_chunk_extra = ChunkExtra::clone(&parent_chunk_extra);
*child_chunk_extra.state_root_mut() = trie_changes.new_root;
*child_chunk_extra.congestion_info_mut() = child_congestion_info;
// Add: reset proposed_split — child shards start with no pending split proposal
if let ChunkExtra::V5(ref mut v5) = child_chunk_extra {
    v5.proposed_split = None;
}
``` [1](#0-0) 

A `proposed_split_mut()` accessor should also be added to `ChunkExtra` alongside the existing `state_root_mut()` and `congestion_info_mut()` for consistency. [7](#0-6) 

### Proof of Concept

1. Enable `ProtocolFeature::DynamicResharding` with a shard above `memory_usage_threshold` (or use `force_split_shards`).
2. Run the network until the shard is split at the epoch N → N+1 boundary.
3. Inspect the parent shard's final `ChunkExtra` — `proposed_split = Some(TrieSplit{...})`.
4. Inspect the child shards' `ChunkExtra` created by `process_memtrie_resharding_storage_update` — `proposed_split = Some(TrieSplit{...})` (stale, inherited).
5. In epoch N+2, observe the chunk producer emitting a chunk with `proposed_split = None`.
6. Observe `InvalidChunkHeaderShardSplit` in `validate_chunk_with_chunk_extra_and_receipts_root`.
7. Observe the child shard permanently stuck: every subsequent block repeats the same rejection because the old-chunk path propagates the stale `ChunkExtra` unchanged. [8](#0-7) [9](#0-8)

### Citations

**File:** chain/chain/src/resharding/manager.rs (L255-280)
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
            chain_store_update.save_state_transition_data(
                *block_hash,
                new_shard_uid.shard_id(),
                parent_trie.recorded_storage(),
                CryptoHash::default(),
                // No contract code is accessed or deployed during resharding.
                // TODO(#11099): Confirm if sending no contracts is ok here.
                Default::default(),
            );

            tracing::info!(target: "resharding", ?new_shard_uid, ?trie_changes.new_root, "child trie created");

            split_shard_trie_changes.trie_changes.insert(*new_shard_uid, trie_changes);
        }
```

**File:** chain/chain/src/runtime/mod.rs (L599-601)
```rust
        if !self.epoch_manager.is_next_block_possibly_last_in_epoch(height, prev_block_hash)? {
            return Ok(None);
        }
```

**File:** chain/chain/src/validate.rs (L132-188)
```rust
/// Validate that all next chunk information matches previous chunk extra.
pub fn validate_chunk_with_chunk_extra_and_receipts_root(
    prev_chunk_extra: &ChunkExtra,
    chunk_header: &ShardChunkHeader,
    outgoing_receipts_root: &CryptoHash,
) -> Result<(), Error> {
    if *prev_chunk_extra.state_root() != chunk_header.prev_state_root() {
        return Err(Error::InvalidStateRoot);
    }

    if prev_chunk_extra.outcome_root() != chunk_header.prev_outcome_root() {
        return Err(Error::InvalidOutcomesProof);
    }

    let chunk_extra_proposals = prev_chunk_extra.validator_proposals();
    let chunk_header_proposals = chunk_header.prev_validator_proposals();
    if chunk_header_proposals.len() != chunk_extra_proposals.len()
        || !chunk_extra_proposals.eq(chunk_header_proposals)
    {
        return Err(Error::InvalidValidatorProposals);
    }

    if prev_chunk_extra.gas_limit() != chunk_header.gas_limit() {
        return Err(Error::InvalidGasLimit);
    }

    if prev_chunk_extra.gas_used() != chunk_header.prev_gas_used() {
        return Err(Error::InvalidGasUsed);
    }

    if prev_chunk_extra.balance_burnt() != chunk_header.prev_balance_burnt() {
        return Err(Error::InvalidBalanceBurnt);
    }

    if outgoing_receipts_root != chunk_header.prev_outgoing_receipts_root() {
        return Err(Error::InvalidReceiptsProof);
    }

    validate_congestion_info(prev_chunk_extra.congestion_info(), chunk_header.congestion_info())?;
    validate_bandwidth_requests(
        prev_chunk_extra.bandwidth_requests(),
        chunk_header.bandwidth_requests(),
    )?;

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

    Ok(())
}
```

**File:** docs/architecture/how/dynamic_resharding.md (L282-282)
```markdown
10. **`chain/chain/src/resharding/manager.rs:249`** -- The resharding manager doesn't set all `ChunkExtra` fields (notably the new `proposed_split` field).
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

**File:** core/primitives/src/types.rs (L968-977)
```rust
        #[inline]
        pub fn state_root_mut(&mut self) -> &mut StateRoot {
            match self {
                Self::V1(v1) => &mut v1.state_root,
                Self::V2(v2) => &mut v2.state_root,
                Self::V3(v3) => &mut v3.state_root,
                Self::V4(v4) => &mut v4.state_root,
                Self::V5(v5) => &mut v5.state_root,
            }
        }
```

**File:** core/primitives/src/sharding/shard_chunk_header_inner.rs (L424-427)
```rust
    /// Proposed split of this shard (dynamic resharding).
    /// `None` if the shard is not above the resharding threshold.
    pub proposed_split: Option<TrieSplit>,
}
```

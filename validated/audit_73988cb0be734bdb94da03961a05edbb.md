### Title
`set_state_finalize_on_height` Blindly Propagates Stale `proposed_split` Through Missing Chunks, Causing `InvalidChunkHeaderShardSplit` on State-Synced Nodes — (`chain/chain/src/chain_update.rs`)

---

### Summary

During state-sync finalization, `set_state_finalize_on_height` processes "old (missing)" chunks by cloning the previous `ChunkExtra` and updating only `state_root`. The `proposed_split` field — which encodes the dynamic-resharding trie-split decision — is silently carried forward from the prior block's `ChunkExtra` instead of being taken from the freshly computed `apply_chunk` result. When the next real chunk arrives from the network, `validate_chunk_with_chunk_extra_and_receipts_root` compares the incoming chunk header's `proposed_split` against the locally stored (stale) `ChunkExtra.proposed_split` and emits `InvalidChunkHeaderShardSplit`, permanently stalling the state-synced node.

---

### Finding Description

**Two divergent sources for the same committed value**

The external report's core pattern is: two systems that share the same underlying asset but use different oracles can produce different prices for the same collateral, enabling arbitrage. The nearcore analog is: two code paths that share the same shard state but use different sources for `proposed_split` produce different values for the same committed field, causing validation rejection.

**Path 1 — normal block processing (correct)**

During ordinary chunk application, `NightshadeRuntime::apply_chunk` calls `compute_proposed_split` and the result flows through `apply_chunk_postprocessing` into a freshly constructed `ChunkExtra`: [1](#0-0) 

`apply_chunk_postprocessing` stores the computed `proposed_split` in the new `ChunkExtra`. The next chunk producer reads this value, embeds it in their chunk header, and `validate_chunk_with_chunk_extra_and_receipts_root` enforces equality: [2](#0-1) 

**Path 2 — state-sync finalization for missing chunks (broken)**

`set_state_finalize_on_height` handles every block between the state-sync anchor and `sync_hash` that has a missing chunk. It calls `apply_chunk` (which internally calls `compute_proposed_split` and produces the correct value for that block height), but then discards the result and manually clones the previous `ChunkExtra`, updating only `state_root`: [3](#0-2) 

The `proposed_split` computed inside `apply_chunk` for the missing chunk is never written to the new `ChunkExtra`. The stale value from the prior block is silently propagated.

**Concrete mismatch scenario**

Consider a shard near the epoch boundary where `is_next_block_possibly_last_in_epoch` returns `true`:

| Block | Event | `ChunkExtra.proposed_split` (local) | Chunk header `proposed_split` (network) |
|-------|-------|--------------------------------------|------------------------------------------|
| H | New chunk applied normally | `None` (not near epoch end) | — |
| H+1 | Missing chunk; `set_state_finalize_on_height` runs | `None` (stale copy from H) | — |
| H+2 | First real chunk after sync | — | `Some(TrieSplit{"aurora", …})` (chunk producer computed from state at H+1) |

At H+2, `validate_chunk_with_chunk_extra_and_receipts_root` compares `ChunkExtra[H+1].proposed_split = None` against `chunk_header[H+2].proposed_split = Some(...)` and returns `InvalidChunkHeaderShardSplit`. The node cannot advance.

The inverse is equally possible: if H had `proposed_split = Some(...)` and H+1 (missing) should have `proposed_split = None`, the stale `Some` is carried forward and the next chunk header's `None` is rejected. [4](#0-3) 

---

### Impact Explanation

A node that completes state sync when any missing chunk falls near an epoch boundary (while `ProtocolFeature::DynamicResharding` is active) will permanently reject valid blocks from the network with `InvalidChunkHeaderShardSplit`. The node cannot self-heal: the stale `ChunkExtra` is committed to the DB, and subsequent block processing always reads it. The node must be wiped and re-synced. If many nodes state-sync simultaneously (e.g., after a network partition or a large validator set rotation), a significant fraction of the network could be simultaneously stalled.

**Impact: High** — liveness failure for any state-synced node that encounters a missing chunk near an epoch boundary under dynamic resharding.

---

### Likelihood Explanation

Dynamic resharding is stabilized in protocol version 153+ (CHANGELOG 2.13.0). Missing chunks are a normal occurrence on mainnet (chunk producers can miss their slot). State sync is the standard bootstrap path for new nodes and for nodes that fall behind. The three conditions co-occur whenever a new node state-syncs into an epoch that had at least one missing chunk in the blocks between the state-sync anchor and `sync_hash` while dynamic resharding is active. This is a routine operational scenario, not a contrived edge case.

---

### Recommendation

In `set_state_finalize_on_height`, after calling `apply_chunk`, extract the `proposed_split` that was computed inside `apply_chunk` and write it into the new `ChunkExtra` instead of carrying the stale value forward. Concretely, `apply_chunk_postprocessing` already handles this correctly for new chunks; the missing-chunk path should either call the same helper or explicitly update `proposed_split` on the cloned `ChunkExtra`:

```rust
// After apply_chunk returns apply_result:
let mut new_chunk_extra = ChunkExtra::clone(&chunk_extra);
*new_chunk_extra.state_root_mut() = apply_result.new_root;
// FIX: also update proposed_split from the apply result
*new_chunk_extra.proposed_split_mut() = apply_result.proposed_split;
self.chain_store_update.save_chunk_extra(..., new_chunk_extra.into());
```

If `ApplyChunkResult` does not yet expose `proposed_split`, it must be added to the result type so the missing-chunk path can consume it.

---

### Proof of Concept

1. Enable `ProtocolFeature::DynamicResharding` (protocol version ≥ 153).
2. Run a network with `epoch_length = 10`. Near block 9 (last of epoch 0), arrange for a chunk to be missing (e.g., drop the chunk producer's network connection for one slot).
3. Start a fresh node and let it state-sync. The `sync_hash` will be inside epoch 1; `set_state_finalize_on_height` will process the missing chunk at block 9.
4. Observe that `ChunkExtra` at block 9 has `proposed_split` copied from block 8 (stale), while the actual chunk producer at block 10 embedded the value computed from the state at block 9.
5. When the fresh node attempts to process block 10, `validate_chunk_with_chunk_extra_and_receipts_root` fires `InvalidChunkHeaderShardSplit` and the node halts.

Relevant code locations: [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** chain/chain/src/runtime/mod.rs (L284-292)
```rust
        let proposed_split = self.compute_proposed_split(
            &trie,
            shard_id,
            &epoch_id,
            current_protocol_version,
            &epoch_config,
            block_height,
            prev_block_hash,
        )?;
```

**File:** chain/chain/src/runtime/mod.rs (L578-620)
```rust
    /// Check if dynamic resharding should be scheduled for the given shard and compute the trie
    /// split. Returns `Some(TrieSplit)` if the shard should be split, `None` otherwise.
    /// Called during `apply_transactions` for every chunk application.
    fn compute_proposed_split(
        &self,
        shard_trie: &Trie,
        shard_id: ShardId,
        epoch_id: &EpochId,
        protocol_version: ProtocolVersion,
        epoch_config: &EpochConfig,
        height: BlockHeight,
        prev_block_hash: &CryptoHash,
    ) -> Result<Option<TrieSplit>, Error> {
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

**File:** chain/chain/src/chain_update.rs (L570-653)
```rust
    /// This method is called when the state sync is finished for a shard. It is
    /// used for applying chunks from after the height included, up until the
    /// sync hash, and storing the results. Those chunks are old (missing).
    pub fn set_state_finalize_on_height(
        &mut self,
        height: BlockHeight,
        shard_id: ShardId,
        sync_hash: CryptoHash,
    ) -> Result<bool, Error> {
        let _span =
            tracing::debug_span!(target: "sync", "set_state_finalize_on_height", height, %shard_id)
                .entered();
        // Note that block headers are already synced and can be taken
        // from store on disk.
        let block_header_result = get_block_header_on_chain_by_height(
            &self.chain_store_update.chain_store(),
            &sync_hash,
            height,
        );
        if let Err(_) = block_header_result {
            // No such height, go ahead.
            return Ok(true);
        }
        let block_header = block_header_result?;
        if block_header.hash() == &sync_hash {
            // Don't continue
            return Ok(false);
        }
        let block = self.chain_store_update.get_block(block_header.hash())?;

        let prev_hash = block_header.prev_hash();
        let prev_block_header = self.chain_store_update.get_block_header(prev_hash)?;

        let shard_uid =
            shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
        let chunk_extra = self.chain_store_update.get_chunk_extra(prev_hash, &shard_uid)?;
        let memtrie_pin = self
            .runtime_adapter
            .get_tries()
            .maybe_pin_memtrie_root(shard_uid, *chunk_extra.state_root())?;

        let apply_result = self.runtime_adapter.apply_chunk(
            RuntimeStorageConfig::new(*chunk_extra.state_root(), true),
            ApplyChunkReason::UpdateTrackedShard,
            ApplyChunkShardContext {
                shard_uid,
                last_validator_proposals: chunk_extra.validator_proposals(),
                gas_limit: chunk_extra.gas_limit(),
                is_new_chunk: false,
                on_post_state_ready: None,
                memtrie_pin,
            },
            ApplyChunkBlockContext::from_header(
                &block_header,
                prev_block_header.next_gas_price(),
                block.block_congestion_info(),
                block.block_bandwidth_requests(),
            ),
            &[],
            SignedValidPeriodTransactions::empty(),
        )?;
        let flat_storage_manager = self.runtime_adapter.get_flat_storage_manager();
        let store_update = flat_storage_manager.save_flat_state_changes(
            *block_header.hash(),
            *prev_block_header.hash(),
            height,
            shard_uid,
            apply_result.trie_changes.state_changes(),
        )?;
        self.chain_store_update.merge(store_update.into());
        self.chain_store_update.save_trie_changes(*block_header.hash(), apply_result.trie_changes);

        // The chunk is missing but some fields may need to be updated
        // anyway. Prepare a chunk extra as a copy of the old chunk
        // extra and apply changes to it.
        let mut new_chunk_extra = ChunkExtra::clone(&chunk_extra);
        *new_chunk_extra.state_root_mut() = apply_result.new_root;
        self.chain_store_update.save_chunk_extra(
            block_header.hash(),
            &shard_uid,
            new_chunk_extra.into(),
        );
        Ok(true)
    }
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

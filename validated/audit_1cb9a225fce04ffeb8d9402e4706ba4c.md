### Title
`StateTransitionData` Never Written During State-Sync Finalization, Permanently Breaking Witness Creation for Post-Sync Chunks — (`File: chain/chain/src/chain_update.rs`)

---

### Summary

`set_state_finalize` and `set_state_finalize_on_height` apply chunks during state-sync finalization but never write `DBCol::StateTransitionData`. `create_state_witness` → `collect_state_transition_data` → `get_state_transition` unconditionally reads that column for every block in the implicit-transition window. For the first chunk a node produces after state sync, every block in that window was processed by the state-sync path, so every `get_state_transition` call returns `Error::Other("Missing transition state proof …")`, making witness creation permanently impossible for that node.

---

### Finding Description

**Write side — state-sync finalization never stores `StateTransitionData`**

`set_state_finalize` (the new-chunk step) calls `apply_chunk_postprocessing` directly without the `should_save_state_transition_data` guard that the normal block-processing path uses: [1](#0-0) 

`set_state_finalize_on_height` (the old-chunk loop) saves `chunk_extra`, trie changes, and flat-state changes, but never calls `save_state_transition_data`: [2](#0-1) 

By contrast, the normal block-processing path (`process_apply_chunk_result`) explicitly saves state-transition data before calling the same helper: [3](#0-2) 

`save_state_transition_data` itself is a no-op when `partial_storage` is `None`, but the state-sync path never even calls it: [4](#0-3) 

**Read side — witness creation requires the column for every block in the implicit-transition window**

`collect_state_transition_data` walks from `chunk_header.prev_block_hash()` back to `prev_chunk_height_included`, calling `get_state_transition` for every block in that range (implicit transitions) and once more for the main-transition block: [5](#0-4) 

`get_state_transition` reads `DBCol::StateTransitionData` and returns a hard error when the row is absent: [6](#0-5) 

**Lifecycle gap — the column is never populated for state-sync blocks**

After state sync completes, `set_state_finalize` processes the block at `chunk.height_included` (main-transition block) and `set_state_finalize_on_height` processes every block from `height_included + 1` to `sync_hash − 1` (implicit-transition blocks). None of those blocks ever receive a `StateTransitionData` row. When the node then produces its first chunk at `sync_hash`, `collect_state_transition_data` walks exactly that range and fails on every `get_state_transition` call.

The `StoredChunkStateTransitionData` struct documents that it must be stored for every chunk, including missing ones: [7](#0-6) 

---

### Impact Explanation

A validator that performs state sync — a routine operation when a node falls behind — cannot produce a `ChunkStateWitness` for its first assigned chunk after sync. `create_state_witness` fails with `"Missing transition state proof"` for every block in the implicit-transition window. Without a valid witness, chunk validators cannot endorse the chunk, the chunk producer is effectively unable to fulfill its protocol duties, and the node accrues kickout risk. The failure is deterministic and permanent for that sync boundary: no subsequent block processing can retroactively fill the missing rows because GC will eventually remove the blocks themselves.

---

### Likelihood Explanation

State sync is a normal, expected operation: validators rejoin after downtime, new validators bootstrap, and nodes that fall more than two epochs behind are forced through the state-sync path. Any such node that is subsequently scheduled as a chunk producer for a shard it just synced will trigger the bug on its very first witness-creation attempt. The trigger requires no adversarial input — it is a structural gap in the state-sync finalization path.

---

### Recommendation

In `set_state_finalize`, save state-transition data for the new-chunk result before (or alongside) calling `apply_chunk_postprocessing`, mirroring the guard in `process_apply_chunk_result`:

```rust
// Before calling apply_chunk_postprocessing in set_state_finalize:
self.chain_store_update.save_state_transition_data(
    *block_header.hash(),
    shard_id,
    apply_result.proof.take(),
    apply_result.applied_receipts_hash,
    mem::take(&mut apply_result.contract_updates),
);
```

In `set_state_finalize_on_height`, add an analogous call after `apply_chunk` returns, matching the `OldChunk` branch of `process_apply_chunk_result`:

```rust
self.chain_store_update.save_state_transition_data(
    *block_header.hash(),
    shard_uid.shard_id(),
    apply_result.proof,
    apply_result.applied_receipts_hash,
    apply_result.contract_updates,
);
```

Both calls should be conditioned on `should_produce_state_witness_for_this_or_next_epoch` (or an equivalent flag passed into the methods) to preserve the storage-space optimization that already exists in the normal path.

---

### Proof of Concept

1. Run a validator node until it falls behind by more than two epochs, triggering state sync.
2. After `set_state_finalize` and the `set_state_finalize_on_height` loop complete, inspect `DBCol::StateTransitionData` for any block hash in the range `[chunk.height_included, sync_hash − 1]` — the column will be empty for all of them.
3. When the node is scheduled as chunk producer for the synced shard, `create_state_witness` is called. `collect_state_transition_data` walks that range and `get_state_transition` returns `Error::Other("Missing transition state proof for block … and shard …")` on the first iteration, aborting witness creation.
4. No state witness is distributed; chunk validators cannot endorse the chunk; the chunk producer accrues a missing-chunk penalty.

The structural gap is visible statically: `set_state_finalize_on_height` (lines 573–653 of `chain_update.rs`) saves `chunk_extra` and trie changes but contains no call to `save_state_transition_data`, while `get_state_transition` (lines 202–216 of `state_witness.rs`) treats a missing row as a hard, non-recoverable error. [8](#0-7) [6](#0-5)

### Citations

**File:** chain/chain/src/chain_update.rs (L131-140)
```rust
                if should_save_state_transition_data {
                    let apply_result = &mut new_chunk_result.apply_result;
                    self.chain_store_update.save_state_transition_data(
                        *block_hash,
                        shard_id,
                        apply_result.proof.take(),
                        apply_result.applied_receipts_hash,
                        mem::take(&mut apply_result.contract_updates),
                    );
                }
```

**File:** chain/chain/src/chain_update.rs (L548-558)
```rust
        let config = self.chain_store_update.chain_store().chunk_persistence_config();
        let new_chunk_result = NewChunkResult { gas_limit, shard_uid, apply_result };
        let mut store_update = self.chain_store_update.store().store_update();
        apply_chunk_postprocessing(
            &mut store_update,
            self.runtime_adapter.as_ref(),
            block.as_ref(),
            new_chunk_result,
            &config,
        )?;
        self.chain_store_update.merge(store_update);
```

**File:** chain/chain/src/chain_update.rs (L573-653)
```rust
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

**File:** chain/chain/src/store/mod.rs (L1643-1663)
```rust
    pub fn save_state_transition_data(
        &mut self,
        block_hash: CryptoHash,
        shard_id: ShardId,
        partial_storage: Option<PartialStorage>,
        applied_receipts_hash: CryptoHash,
        contract_updates: ContractUpdates,
    ) {
        if let Some(partial_storage) = partial_storage {
            let ContractUpdates { contract_accesses, contract_deploys } = contract_updates;
            self.state_transition_data.insert(
                (block_hash, shard_id),
                StoredChunkStateTransitionData::V1(StoredChunkStateTransitionDataV1 {
                    base_state: partial_storage.nodes,
                    receipts_hash: applied_receipts_hash,
                    contract_accesses: contract_accesses.into_iter().collect(),
                    contract_deploys: contract_deploys.into_iter().map(|c| c.into()).collect(),
                }),
            );
        }
    }
```

**File:** chain/chain/src/stateless_validation/state_witness.rs (L100-191)
```rust
    fn collect_state_transition_data(
        &self,
        epoch_manager: &dyn EpochManagerAdapter,
        chunk_header: &ShardChunkHeader,
        prev_chunk_header: &ShardChunkHeader,
    ) -> Result<StateTransitionData, Error> {
        let prev_chunk_height_included = prev_chunk_header.height_included();

        // Iterate over blocks in chain from `chunk_header.prev_block_hash()`
        // (inclusive) until the block with height `prev_chunk_height_included`
        // (exclusive).
        // Every block corresponds to one implicit state transition between
        // `prev_chunk_header` and `chunk_header`.
        // There may be one additional implicit transition for a block, if
        // resharding happens after its processing.
        // TODO(logunov): consider uniting with `get_incoming_receipts_for_shard`
        // because it has the same purpose.
        let mut current_block_hash = *chunk_header.prev_block_hash();
        let mut next_epoch_id = epoch_manager.get_epoch_id_from_prev_block(&current_block_hash)?;
        let mut next_shard_id = chunk_header.shard_id();
        let mut implicit_transitions = vec![];

        loop {
            let header = self.get_block_header(&current_block_hash)?;
            if header.height() < prev_chunk_height_included {
                return Err(Error::InvalidBlockHeight(prev_chunk_height_included));
            }

            let current_epoch_id = *header.epoch_id();
            let current_shard_id = epoch_manager
                .get_prev_shard_id_from_prev_hash(&current_block_hash, next_shard_id)?
                .1;
            if current_shard_id != next_shard_id {
                // If shard id changes, we need to get implicit state
                // transition from current shard id to the next shard id.
                let (chunk_state_transition, _, _) = self.get_state_transition(
                    epoch_manager,
                    &current_block_hash,
                    &next_epoch_id,
                    next_shard_id,
                )?;
                implicit_transitions.push(chunk_state_transition);
            }
            next_epoch_id = current_epoch_id;
            next_shard_id = current_shard_id;

            if header.height() == prev_chunk_height_included {
                break;
            }

            // Add implicit state transition.
            let (chunk_state_transition, _, _) = self.get_state_transition(
                epoch_manager,
                &current_block_hash,
                &current_epoch_id,
                current_shard_id,
            )?;
            implicit_transitions.push(chunk_state_transition);

            current_block_hash = *header.prev_hash();
        }

        let main_block = current_block_hash;
        let epoch_id = next_epoch_id;
        let main_transition_shard_id = next_shard_id;
        implicit_transitions.reverse();

        // Get the main state transition.
        let (main_transition, receipts_hash, contract_updates) = if prev_chunk_header.is_genesis() {
            self.get_genesis_state_transition(
                epoch_manager,
                &main_block,
                &epoch_id,
                main_transition_shard_id,
            )?
        } else {
            self.get_state_transition(
                epoch_manager,
                &main_block,
                &epoch_id,
                main_transition_shard_id,
            )?
        };

        Ok(StateTransitionData {
            main_transition,
            main_transition_shard_id,
            implicit_transitions,
            applied_receipts_hash: receipts_hash,
            contract_updates,
        })
    }
```

**File:** chain/chain/src/stateless_validation/state_witness.rs (L202-216)
```rust
        let stored_chunk_state_transition_data = self
            .store()
            .get_ser(
                near_store::DBCol::StateTransitionData,
                &near_primitives::utils::get_block_shard_id(block_hash, shard_id),
            )
            .ok_or_else(|| {
                let message = format!(
                    "Missing transition state proof for block {block_hash} and shard {shard_id}"
                );
                if !cfg!(feature = "shadow_chunk_validation") {
                    log_assert_fail!("{message}");
                }
                Error::Other(message)
            })?;
```

**File:** core/primitives/src/stateless_validation/stored_chunk_state_transition_data.rs (L7-14)
```rust
/// Stored on disk for each chunk, including missing chunks, in order to
/// produce a chunk state witness when needed.
#[derive(Debug, BorshSerialize, BorshDeserialize, ProtocolSchema)]
#[borsh(use_discriminant = true)]
#[repr(u8)]
pub enum StoredChunkStateTransitionData {
    V1(StoredChunkStateTransitionDataV1) = 0,
}
```

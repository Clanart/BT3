Let me look more closely at the exact GC deletion logic and the `collect_state_transition_data` loop to trace the precise data dependency.

The code is clear enough to render a definitive verdict. Here is the full analysis:

---

### Title
GC Prematurely Deletes Parent-Shard `StateTransitionData` After Resharding, Breaking Witness Production for Child Shards — (`chain/chain/src/garbage_collection.rs`)

### Summary

`clear_state_transition_data` builds `relevant_shards` from only the **current** and **next** epoch shard layouts. After a resharding boundary (epoch E → epoch E+1, parent shard P → children C1/C2), the parent shard P is absent from both layouts. As soon as the finalized block enters epoch E+1, every `DBCol::StateTransitionData` entry keyed by P is unconditionally deleted. However, `collect_state_transition_data` still needs those entries to produce the state witness for the first new chunk of C1 or C2 — specifically the main transition and any implicit transitions that cross the epoch boundary. If GC runs before that witness is produced (which happens whenever C1/C2 have one or more missing chunks at the start of epoch E+1), `get_state_transition` returns `Error::Other("Missing transition state proof …")` and witness production fails entirely for those child-shard chunks.

### Finding Description

**GC deletion path** — `clear_state_transition_data` (lines 325–353):

```rust
let relevant_shards: HashSet<_> = {
    let shard_layout = epoch_manager
        .get_shard_layout(final_block.header().epoch_id())       // epoch E+1: {C1, C2}
        .expect("epoch id must exist");
    let next_epoch_shard_layout = epoch_manager
        .get_shard_layout(final_block.header().next_epoch_id())  // epoch E+2: {C1, C2}
        .expect("next epoch id must exist");
    shard_layout.shard_ids().chain(next_epoch_shard_layout.shard_ids()).collect()
    // P is never added
};
…
let Some(final_block_height) = final_block_chunk_created_heights.get(&shard_id) else {
    // P is absent from the final block's chunks (final block is in epoch E+1)
    if !relevant_shards.contains(&shard_id) {   // P not in {C1, C2}
        store_update.delete(DBCol::StateTransitionData, &key);   // ← deletes ALL P entries
    }
    continue;
};
``` [1](#0-0) 

**Witness production path** — `collect_state_transition_data` (lines 122–182):

When producing a witness for the first new chunk of C1 at height H_new, with `prev_chunk_header` = last chunk of P at height H_P, the loop walks backwards from H_new−1 to H_P. At the epoch boundary block H_last (last block of epoch E):

- `current_shard_id = P`, `next_shard_id = C1` → resharding implicit transition for C1 at H_last is fetched (this is preserved by GC because C1 ∈ `relevant_shards`)
- `next_shard_id` is then set to P
- For every block from H_P to H_last−1 still in epoch E: `get_state_transition(…, P)` is called for implicit transitions — **these entries were deleted by GC**
- After the loop: `get_state_transition(…, &H_P, &epoch_E, P)` is called for the main transition — **also deleted** [2](#0-1) 

`get_state_transition` returns a hard error when the DB entry is absent:

```rust
.ok_or_else(|| {
    let message = format!(
        "Missing transition state proof for block {block_hash} and shard {shard_id}"
    );
    if !cfg!(feature = "shadow_chunk_validation") {
        log_assert_fail!("{message}");
    }
    Error::Other(message)
})?;
``` [3](#0-2) 

### Impact Explanation

The exact corrupted (deleted) value is: `DBCol::StateTransitionData` key `(block_hash_at_H_P, parent_shard_id_P)` — and every analogous key `(block_hash_at_H, P)` for H in [H_P, H_last−1].

`create_state_witness` propagates the error up through `send_chunk_state_witness_to_chunk_validators`. No witness is distributed to chunk validators for the first new chunk of C1 (or C2) after resharding. Chunk validators never receive a witness, so they cannot endorse the chunk. The chunk producer's witness-production capability for those child shards is permanently broken until the node is restarted or the data is somehow restored — neither of which is automatic. [4](#0-3) 

### Likelihood Explanation

The race is narrow but realistic:

1. Resharding occurs at the epoch boundary (E → E+1).
2. The chunk producer for C1 misses one or more chunks at the very start of epoch E+1 (e.g., it is briefly offline, or the block producer for those heights is on a fork).
3. During those missing-chunk blocks, the finalized block advances into epoch E+1 (finality lags ~2 blocks, so only 3 consecutive blocks of epoch E+1 need to be produced for the final block to reach H1).
4. GC ticks (every 500 ms) and runs `clear_state_transition_data` with `final_block` in epoch E+1 — deleting all P entries.
5. The first new chunk of C1 is produced at H_{k+1}; `create_state_witness` fails.

No privileged action is required. Missing chunks at epoch boundaries are a normal operational occurrence.

### Recommendation

`relevant_shards` must also include the **previous** epoch's shard layout when the final block is the first block of a new epoch (i.e., when a resharding just occurred). Concretely, add:

```rust
if let Ok(prev_epoch_id) = epoch_manager.get_prev_epoch_id_from_prev_block(
    final_block.header().prev_hash()
) {
    if let Ok(prev_shard_layout) = epoch_manager.get_shard_layout(&prev_epoch_id) {
        relevant_shards.extend(prev_shard_layout.shard_ids());
    }
}
```

Alternatively, the height-based deletion branch (lines 355–360) should be extended to cover parent shards: instead of deleting all P entries unconditionally, only delete entries whose block height is strictly less than the height of the last chunk of P in epoch E. [5](#0-4) 

### Proof of Concept

The existing test `test_state_transition_data_gc_when_resharding` only asserts that old shard data is eventually cleared — it does not assert that parent-shard data survives long enough for witness production.

A failing test-loop test would:
1. Configure resharding (3-shard base layout → 4-shard layout).
2. At the epoch boundary, pause the chunk producer for one child shard for 3 blocks (so the final block enters epoch E+1 while no new chunk for that child has been produced).
3. Let GC tick.
4. Resume the chunk producer and produce the first new chunk for the child shard.
5. Assert that `create_state_witness` succeeds and that `DBCol::StateTransitionData` for the parent shard at `H_P` is still present at the moment `get_state_transition` is called.

The assertion at step 5 would fail because GC deleted the parent-shard entries at step 3. [6](#0-5) [7](#0-6)

### Citations

**File:** chain/chain/src/garbage_collection.rs (L299-367)
```rust
    fn clear_state_transition_data(
        &self,
        epoch_manager: &dyn EpochManagerAdapter,
    ) -> Result<(), Error> {
        let _metric_timer = metrics::STATE_TRANSITION_DATA_GC_TIME.start_timer();

        let Ok(last_block_header) = self.get_block_header(&self.head()?.last_block_hash) else {
            // This can happen if the node just did state sync.
            tracing::debug!(head = ?self.head()?, "could not get head header");
            return Ok(());
        };
        let final_block_hash = last_block_header.last_final_block();
        if final_block_hash == &CryptoHash::default() {
            return Ok(());
        }
        let Ok(final_block) = self.get_block(final_block_hash) else {
            // This can happen if the node just did state sync.
            tracing::debug!(target: "garbage_collection", ?final_block_hash, "could not get final block");
            return Ok(());
        };
        let final_block_chunk_created_heights: HashMap<_, _> = final_block
            .chunks()
            .iter()
            .map(|chunk| (chunk.shard_id(), chunk.height_created()))
            .collect();

        let relevant_shards: HashSet<_> = {
            let shard_layout = epoch_manager
                .get_shard_layout(final_block.header().epoch_id())
                .expect("epoch id must exist");
            let next_epoch_shard_layout = epoch_manager
                .get_shard_layout(final_block.header().next_epoch_id())
                .expect("next epoch id must exist");
            shard_layout.shard_ids().chain(next_epoch_shard_layout.shard_ids()).collect()
        };

        let mut total_entries = 0;
        let mut entries_cleared = 0;
        let mut store_update = self.store().store_update();
        for (key, _) in self.store().iter(DBCol::StateTransitionData) {
            total_entries += 1;
            let (block_hash, shard_id) = get_block_shard_id_rev(&key).map_err(|err| {
                Error::StorageError(near_store::StorageError::StorageInconsistentState(format!(
                    "Invalid StateTransitionData key: {err:?}"
                )))
            })?;

            let Some(final_block_height) = final_block_chunk_created_heights.get(&shard_id) else {
                if !relevant_shards.contains(&shard_id) {
                    store_update.delete(DBCol::StateTransitionData, &key);
                    entries_cleared += 1;
                }
                // StateTransitionData may correspond to the shard that is created in next epoch.
                continue;
            };

            // If we are unable to find block_height in store, it is likely the block is already GC'd
            let block_height = self.get_block_height(&block_hash);
            if block_height.is_err() || block_height.unwrap() < *final_block_height {
                store_update.delete(DBCol::StateTransitionData, &key);
                entries_cleared += 1;
            }
        }

        metrics::STATE_TRANSITION_DATA_GC_TOTAL_ENTRIES.set(total_entries);
        store_update.commit();
        metrics::STATE_TRANSITION_DATA_GC_CLEARED_ENTRIES.inc_by(entries_cleared);
        Ok(())
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

**File:** chain/client/src/stateless_validation/state_witness_producer.rs (L45-51)
```rust
        let CreateWitnessResult { state_witness, main_transition_shard_id, contract_updates } =
            self.chain.chain_store().create_state_witness(
                self.epoch_manager.as_ref(),
                prev_block_header,
                prev_chunk_header,
                chunk,
            )?;
```

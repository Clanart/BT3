### Title
`MainStateTransitionCache` Written with Resharding-Reassigned Receipts Before Implicit Transitions, Causing Double-Reassignment on Cache Hit — (`chain/chain/src/stateless_validation/chunk_validation.rs`)

### Summary

In `validate_chunk_state_witness_impl`, the shared `MainStateTransitionCache` is populated with `outgoing_receipts` that have **already been mutated by `reassign_outgoing_receipts_for_resharding`** (lines 634–646), but the cache write occurs at lines 647–660. Any subsequent validation for the same `(witness_chunk_shard_uid, block_hash)` reads those pre-mutated receipts from the cache and then applies the resharding reassignment **a second time**, producing a wrong `outgoing_receipts_root` and causing the validation to fail with `InvalidReceiptsProof`.

### Finding Description

The execution order inside `validate_chunk_state_witness_impl` is:

1. **Lines 594–621**: Read cache. On miss, apply main transition → produce `chunk_extra` (state root after main transition) and `outgoing_receipts` (original).
2. **Lines 633–646**: If `chunk_shard_layout != witness_shard_layout` (resharding epoch boundary), **mutate** `outgoing_receipts` in-place via `reassign_outgoing_receipts_for_resharding`. Compute `outgoing_receipts_hashes` from the now-mutated receipts.
3. **Lines 647–660**: Write to `MainStateTransitionCache` — storing `chunk_extra.clone()` and `outgoing_receipts.clone()`. At this point `outgoing_receipts` is already resharding-reassigned.
4. **Lines 672–750**: Apply implicit transitions, mutating `chunk_extra.state_root` further.

When a second concurrent validation for the same `(witness_chunk_shard_uid, block_hash)` executes:

- **Cache hit** at line 620: `outgoing_receipts` is loaded from the cache — already resharding-reassigned.
- **Lines 633–646**: `chunk_shard_layout != witness_shard_layout` is still true (same epoch boundary), so `reassign_outgoing_receipts_for_resharding` is applied **again** to already-reassigned receipts.
- The resulting `outgoing_receipts_hashes` is computed from doubly-reassigned receipts → wrong `outgoing_receipts_root`.
- `validate_chunk_with_chunk_extra_and_receipts_root` at line 755 fails with `InvalidReceiptsProof`.
- The validator never sends a chunk endorsement for this attempt.
- The cache is then **overwritten** at lines 647–660 with the doubly-reassigned receipts, poisoning it for any future validation sharing the same cache key.

The poisoned cache entry also affects a structurally distinct scenario: two different chunks (at different heights) that share the same main-transition `block_hash` (possible when a shard has consecutive missing chunks in the same epoch). Witness W1 for chunk C1 populates the cache with resharding-reassigned receipts; witness W2 for chunk C2 reads the poisoned entry, applies the reassignment again, and fails — even though W2 is the first and only validation attempt for C2. [1](#0-0) [2](#0-1) 

### Impact Explanation

During a resharding epoch boundary, any validator node that concurrently processes two witnesses sharing the same `(witness_chunk_shard_uid, block_hash)` — or processes a second chunk whose main-transition block hash matches a previously cached (now poisoned) entry — will fail to produce a chunk endorsement for the affected chunk. If a sufficient number of validators are affected, the chunk will not accumulate the required endorsement stake and will be excluded from the block, causing a liveness failure for that shard at the resharding boundary.

The poisoned cache entry persists for the lifetime of the `LruCache` (up to `NUM_WITNESS_RESULT_CACHE_ENTRIES = 20` entries per shard), meaning all subsequent cache hits for the same key will also fail. [3](#0-2) [4](#0-3) 

### Likelihood Explanation

The condition `chunk_shard_layout != witness_shard_layout` is true at every resharding epoch boundary. Concurrent validation of two witnesses sharing the same main-transition `block_hash` is a normal occurrence: the `validation_spawner` spawns validations in parallel threads with no deduplication guard, and network retransmission of a valid witness (which any peer can replay, since the witness is already signed by the chunk producer) is sufficient to trigger the race. The "two chunks, same main-transition block" scenario arises naturally whenever a shard has consecutive missing chunks within the same epoch. [5](#0-4) [6](#0-5) 

### Recommendation

Move the `MainStateTransitionCache` write to **before** the resharding reassignment, so the cache stores the original (pre-reassignment) `outgoing_receipts`. Each validation then applies the resharding reassignment to its own local copy, independently of what is in the cache.

```rust
// Save main state transition result to cache BEFORE resharding reassignment.
{
    let mut shard_cache = main_state_transition_cache.lock();
    let cache = shard_cache.entry(witness_chunk_shard_uid).or_insert_with(|| {
        LruCache::new(NonZeroUsize::new(NUM_WITNESS_RESULT_CACHE_ENTRIES).unwrap())
    });
    cache.put(
        block_hash,
        ChunkStateWitnessValidationResult {
            chunk_extra: chunk_extra.clone(),
            outgoing_receipts: outgoing_receipts.clone(), // original, not yet reassigned
        },
    );
}

// Now apply resharding reassignment to the local copy only.
let outgoing_receipts_hashes = {
    let chunk_epoch_id = epoch_manager.get_epoch_id(&block_hash)?;
    let chunk_shard_layout = epoch_manager.get_shard_layout(&chunk_epoch_id)?;
    if chunk_shard_layout != witness_shard_layout {
        ChainStore::reassign_outgoing_receipts_for_resharding(
            &mut outgoing_receipts,
            &witness_shard_layout,
            witness_chunk_shard_id,
            shard_id,
        )?;
    }
    Chain::build_receipts_hashes(&outgoing_receipts, &witness_shard_layout)?
};
``` [7](#0-6) 

### Proof of Concept

**Scenario: concurrent validation of the same witness during resharding**

1. Epoch boundary: `chunk_shard_layout != witness_shard_layout` is true for shard S.
2. Validator V receives witness W for chunk C (height H, shard S). `validation_spawner` spawns Thread-1.
3. A peer replays W to V. `validation_spawner` spawns Thread-2.
4. Thread-1: cache miss → computes main transition → `outgoing_receipts = [r1, r2]` → applies resharding reassignment → `outgoing_receipts = [r1']` → writes `{chunk_extra, outgoing_receipts=[r1']}` to cache → validation succeeds → endorsement sent.
5. Thread-2: cache hit → reads `outgoing_receipts = [r1']` → applies resharding reassignment again → `outgoing_receipts = [r1'']` (wrong) → computes wrong `outgoing_receipts_root` → `validate_chunk_with_chunk_extra_and_receipts_root` returns `InvalidReceiptsProof` → no endorsement sent → cache overwritten with `outgoing_receipts=[r1'']`.
6. Any subsequent validation for the same `(witness_chunk_shard_uid, block_hash)` — including a different chunk C2 sharing the same main-transition block hash — reads `[r1'']`, applies reassignment a third time, and also fails. [8](#0-7) [9](#0-8)

### Citations

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L94-99)
```rust
pub type MainStateTransitionCache =
    Arc<Mutex<HashMap<ShardUId, LruCache<CryptoHash, ChunkStateWitnessValidationResult>>>>;

/// The number of state witness validation results to cache per shard.
/// This number needs to be small because result contains outgoing receipts, which can be large.
const NUM_WITNESS_RESULT_CACHE_ENTRIES: usize = 20;
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L565-660)
```rust
pub fn validate_chunk_state_witness_impl(
    state_witness: ChunkStateWitness,
    pre_validation_output: PreValidationOutput,
    epoch_manager: &dyn EpochManagerAdapter,
    runtime_adapter: &dyn RuntimeAdapter,
    main_state_transition_cache: &MainStateTransitionCache,
    rs: Arc<ReedSolomon>,
) -> Result<(), Error> {
    let ChunkProductionKey { shard_id: witness_chunk_shard_id, epoch_id, height_created } =
        state_witness.chunk_production_key();
    let _timer = crate::stateless_validation::metrics::CHUNK_STATE_WITNESS_VALIDATION_TIME
        .with_label_values(&[&witness_chunk_shard_id.to_string()])
        .start_timer();
    let span = tracing::debug_span!(
        target: "client",
        "validate_chunk_state_witness",
        height = height_created,
        shard_id = %witness_chunk_shard_id,
        tag_block_production = true,
        tag_witness_distribution = true,
    )
    .entered();
    let witness_shard_layout = epoch_manager.get_shard_layout(&epoch_id)?;
    let witness_chunk_shard_uid =
        shard_id_to_uid(epoch_manager, witness_chunk_shard_id, &epoch_id)?;
    let block_hash = pre_validation_output.main_transition_params.block_hash();
    let epoch_id = epoch_manager.get_epoch_id(&block_hash)?;
    let shard_id = pre_validation_output.main_transition_params.shard_id();
    let shard_uid = shard_id_to_uid(epoch_manager, shard_id, &epoch_id)?;
    let cache_result = {
        let mut shard_cache = main_state_transition_cache.lock();
        shard_cache
            .get_mut(&witness_chunk_shard_uid)
            .and_then(|cache| cache.get(&block_hash).cloned())
    };
    let (mut chunk_extra, mut outgoing_receipts) =
        match (pre_validation_output.main_transition_params, cache_result) {
            (MainTransition::Genesis { chunk_extra, .. }, _) => (chunk_extra, vec![]),
            (MainTransition::NewChunk { new_chunk_data, .. }, None) => {
                let chunk_gas_limit = new_chunk_data.gas_limit;
                let NewChunkResult { apply_result: mut main_apply_result, .. } = apply_new_chunk(
                    ApplyChunkReason::ValidateChunkStateWitness,
                    &span,
                    new_chunk_data,
                    ShardContext { shard_uid, should_apply_chunk: true },
                    runtime_adapter,
                    // Recorded-storage replay; no memtrie path.
                    MaybePinnedMemtrieRoot::no_memtries(),
                    None,
                )?;
                let outgoing_receipts = std::mem::take(&mut main_apply_result.outgoing_receipts);
                let chunk_extra = main_apply_result.to_chunk_extra(chunk_gas_limit);

                (chunk_extra, outgoing_receipts)
            }
            (_, Some(result)) => (result.chunk_extra, result.outgoing_receipts),
        };
    if chunk_extra.state_root() != &state_witness.main_state_transition().post_state_root {
        // This is an early check, it's not for correctness, only for better
        // error reporting in case of an invalid state witness due to a bug.
        // Only the final state root check against the chunk header is required.
        return Err(Error::InvalidChunkStateWitness(format!(
            "Post state root {:?} for main transition does not match expected post state root {:?}",
            chunk_extra.state_root(),
            state_witness.main_state_transition().post_state_root,
        )));
    }

    // Compute receipt hashes here to avoid copying receipts
    let outgoing_receipts_hashes = {
        let chunk_epoch_id = epoch_manager.get_epoch_id(&block_hash)?;
        let chunk_shard_layout = epoch_manager.get_shard_layout(&chunk_epoch_id)?;
        if chunk_shard_layout != witness_shard_layout {
            ChainStore::reassign_outgoing_receipts_for_resharding(
                &mut outgoing_receipts,
                &witness_shard_layout,
                witness_chunk_shard_id,
                shard_id,
            )?;
        }
        Chain::build_receipts_hashes(&outgoing_receipts, &witness_shard_layout)?
    };
    // Save main state transition result to cache.
    {
        let mut shard_cache = main_state_transition_cache.lock();
        let cache = shard_cache.entry(witness_chunk_shard_uid).or_insert_with(|| {
            LruCache::new(NonZeroUsize::new(NUM_WITNESS_RESULT_CACHE_ENTRIES).unwrap())
        });
        cache.put(
            block_hash,
            ChunkStateWitnessValidationResult {
                chunk_extra: chunk_extra.clone(),
                outgoing_receipts: outgoing_receipts.clone(),
            },
        );
    }
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L672-750)
```rust
    for (implicit_transition_params, transition) in pre_validation_output
        .implicit_transition_params
        .into_iter()
        .zip(state_witness.implicit_transitions().into_iter())
    {
        let (shard_uid, new_state_root, new_congestion_info) = match implicit_transition_params {
            ImplicitTransitionParams::ApplyOldChunk(block, shard_uid) => {
                let shard_context = ShardContext { shard_uid, should_apply_chunk: false };
                let old_chunk_data = OldChunkData {
                    prev_chunk_extra: chunk_extra.clone(),
                    block,
                    storage_context: StorageContext {
                        storage_data_source: StorageDataSource::Recorded(PartialStorage {
                            nodes: transition.base_state.clone(),
                        }),
                        state_patch: Default::default(),
                    },
                };
                let OldChunkResult { apply_result, .. } = apply_old_chunk(
                    ApplyChunkReason::ValidateChunkStateWitness,
                    &span,
                    old_chunk_data,
                    shard_context,
                    runtime_adapter,
                    // Recorded-storage replay; no memtrie path.
                    MaybePinnedMemtrieRoot::no_memtries(),
                )?;
                let congestion_info = chunk_extra.congestion_info();
                (shard_uid, apply_result.new_root, congestion_info)
            }
            ImplicitTransitionParams::Resharding(
                boundary_account,
                retain_mode,
                child_shard_uid,
            ) => {
                let old_root = *chunk_extra.state_root();
                let partial_storage = PartialStorage { nodes: transition.base_state.clone() };
                let parent_trie = Trie::from_recorded_storage(partial_storage, old_root, true);

                // Update the congestion info based on the parent shard. It's
                // important to do this step before the `retain_split_shard`
                // because only the parent trie has the needed information.
                let epoch_id = epoch_manager.get_epoch_id(&block_hash)?;
                let parent_shard_layout = epoch_manager.get_shard_layout(&epoch_id)?;
                let parent_congestion_info = chunk_extra.congestion_info();

                let child_epoch_id = epoch_manager.get_next_epoch_id(&block_hash)?;
                let child_shard_layout = epoch_manager.get_shard_layout(&child_epoch_id)?;
                let child_congestion_info = ReshardingManager::get_child_congestion_info(
                    &parent_trie,
                    &parent_shard_layout,
                    parent_congestion_info,
                    &child_shard_layout,
                    &child_shard_uid,
                    retain_mode,
                )?;

                let trie_changes =
                    parent_trie.retain_split_shard(&boundary_account, retain_mode)?;

                (child_shard_uid, trie_changes.new_root, child_congestion_info)
            }
        };

        *chunk_extra.state_root_mut() = new_state_root;
        *chunk_extra.congestion_info_mut() = new_congestion_info;
        if chunk_extra.state_root() != &transition.post_state_root {
            // This is an early check, it's not for correctness, only for better
            // error reporting in case of an invalid state witness due to a bug.
            // Only the final state root check against the chunk header is required.
            return Err(Error::InvalidChunkStateWitness(format!(
                "Post state root {:?} for implicit transition at block {:?} to shard {:?}, does not match expected state root {:?}",
                chunk_extra.state_root(),
                transition.block_hash,
                shard_uid,
                transition.post_state_root
            )));
        }
    }
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L751-777)
```rust
    // Compute receipts root + header validation in parallel with encoded-merkle-root check.
    let (res_receipts_root, res_encoded_merkle_check) = rayon::join(
        || -> Result<CryptoHash, Error> {
            let (outgoing_receipts_root, _) = merklize(&outgoing_receipts_hashes);
            validate_chunk_with_chunk_extra_and_receipts_root(
                &chunk_extra,
                &state_witness.chunk_header(),
                &outgoing_receipts_root,
            )?;
            Ok(outgoing_receipts_root)
        },
        || {
            let (tx_root, _) = merklize(&state_witness.new_transactions());
            if tx_root != *state_witness.chunk_header().tx_root() {
                return Err(Error::InvalidTxRoot);
            }
            validate_chunk_with_encoded_merkle_root(
                &state_witness.chunk_header(),
                &outgoing_receipts,
                state_witness.new_transactions(),
                rs.as_ref(),
                shard_id,
            )
        },
    );
    res_receipts_root?;
    res_encoded_merkle_check?;
```

**File:** chain/client/src/stateless_validation/chunk_validation_actor.rs (L69-83)
```rust
pub struct ChunkValidationActor {
    chain_store: ChainStore,
    genesis_block: Arc<Block>,
    epoch_manager: Arc<dyn EpochManagerAdapter>,
    runtime_adapter: Arc<dyn RuntimeAdapter>,
    network_adapter: Sender<PeerManagerMessageRequest>,
    validator_signer: MutableValidatorSigner,
    save_latest_witnesses: bool,
    save_invalid_witnesses: bool,
    validation_spawner: Arc<dyn AsyncComputationSpawner>,
    main_state_transition_result_cache: MainStateTransitionCache,
    orphan_witness_pool: Arc<Mutex<OrphanStateWitnessPool>>,
    max_orphan_witness_size: u64,
    rs: Arc<ReedSolomon>,
}
```

**File:** chain/client/src/stateless_validation/chunk_validation_actor.rs (L485-520)
```rust
        self.validation_spawner.spawn("stateless_validation", move || {
            // Capture the processing_done_tracker here - it will be dropped when this closure completes
            let _processing_done_tracker = processing_done_tracker;

            match chunk_validation::validate_chunk_state_witness(
                state_witness,
                pre_validation_result,
                epoch_manager.as_ref(),
                runtime_adapter.as_ref(),
                &cache,
                store,
                save_witness_if_invalid,
                rs,
            ) {
                Ok(_) => {
                    send_chunk_endorsement_to_block_producers(
                        &chunk_header,
                        epoch_manager.as_ref(),
                        signer.as_ref(),
                        &network_adapter,
                    );
                }
                Err(err) => {
                    near_chain::stateless_validation::metrics::CHUNK_WITNESS_VALIDATION_FAILED_TOTAL
                        .with_label_values(&[shard_id.to_string().as_str(), err.prometheus_label_value()])
                        .inc();
                    tracing::error!(
                        target: "chunk_validation",
                        ?err,
                        ?chunk_producer_name,
                        ?chunk_production_key,
                        "failed to validate chunk state witness"
                    );
                }
            }
        });
```

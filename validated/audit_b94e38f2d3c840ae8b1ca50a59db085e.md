## Title
Unbounded global program-cache flatten/eviction triggered on every missed-program transaction batch causes shared compute-cost DoS - ([File: program-runtime/src/loaded_programs.rs])

## Summary
This report's underlying bug class is the NFTX `distribute()` pattern: a per-user-transaction loop over a list whose length is attacker-growable, causing shared-cost gas/compute blowup that eventually blocks unrelated user operations. In Agave, `ProgramCache::evict_using_random_selection()` and `sort_and_unload()` are called from the SVM's shared `TransactionBatchProcessor::load_and_execute_sanitized_transactions` whenever a batch loads a program that was missing from the cache, and both begin by calling `get_flattened_entries()`, which flattens *every* entry across *every* cached program id and slot-version into a `Vec` before sampling/sorting [1](#0-0) [2](#0-1) .

## Finding Description
`ProgramCache<FG>::index` stores a `HashMap<Pubkey, Vec<Arc<ProgramCacheEntry>>>` of every program ever deployed/observed on any live fork [3](#0-2) . Any unprivileged user can permissionlessly deploy new BPF programs via the (non-excluded, non-LoaderV4) BPF Loader; each newly-deployed program id becomes a new, permanent key in this cache map that is never evicted (only its compiled bytecode is "unloaded", the entry itself persists until pruned by rerooting logic).

`get_flattened_entries()` performs a full `entries.iter().flat_map(...)` over the *entire* map, materializing a `Vec<(Pubkey, Slot, Arc<ProgramCacheEntry>)>` for all cached programs [1](#0-0) . This vector is used both by `sort_and_unload()` (full sort by usage) and by `evict_using_random_selection()` (samples/`swap_remove`s from the full candidate vector) [2](#0-1) .

`evict_using_random_selection()` is invoked directly from the hot transaction-processing path in `TransactionBatchProcessor::load_and_execute_sanitized_transactions`:
```
if program_cache_for_tx_batch.loaded_missing || program_cache_for_tx_batch.merged_modified {
    const SHRINK_LOADED_PROGRAMS_TO_PERCENTAGE: Percent = 90;
    self.global_program_cache
        .write()
        .unwrap()
        .evict_using_random_selection(SHRINK_LOADED_PROGRAMS_TO_PERCENTAGE, self.slot);
}
``` [4](#0-3) 

`loaded_missing` becomes true whenever any transaction in the batch references a program not currently present in the per-batch cache and must be fetched from the global cache — a condition trivially reachable by any unprivileged user simply by invoking a program that isn't already hot in the working set (including their own freshly-deployed programs). Because this check gates a write-lock on the single, validator-wide `global_program_cache`, every subsequent transaction batch (belonging to *other* unrelated users) that also needs to load a missing program will serialize behind this same lock and pay the cost of flattening the whole cache again.

The broken invariant is: the cost of `get_flattened_entries()` (and thus of `evict_using_random_selection`/`sort_and_unload`) scales linearly with the total number of distinct programs ever cached across the whole cluster's history of deployments on live forks, not with anything bounded per-transaction or per-block — exactly the same "unbounded loop over an attacker-growable receiver list, triggered inside a per-transaction operation on shared state" pattern flagged in the NFTX report's `distribute()`/`feeReceivers` loop.

## Impact Explanation
This does not directly cause fund loss, but it matches the accepted "non-RPC remote exhaustion/crash" and "false execution/consensus" risk categories: as the number of distinct on-chain programs grows (an unprivileged, permissionless, cheap action — deploying programs costs only rent-exempt minimum + a few KB of data), the per-batch cost of `evict_using_random_selection`/`sort_and_unload` grows without bound. Because this routine runs under a global `RwLock` shared by every transaction batch on the validator (both banking-stage packing and replay), a single attacker who deploys a large number of programs and then submits transactions that trigger cache misses can inflate the CPU/allocation cost paid by *every* concurrent transaction batch that also misses the cache, degrading block production/replay throughput for unrelated users' transactions — a shared-resource denial-of-service, not bounded by any per-transaction compute-unit budget (this work happens outside the SVM's CU metering).

## Likelihood Explanation
Triggering `loaded_missing=true` requires only that a transaction references a program not already resident in the working per-batch program cache — an everyday, unprivileged occurrence (e.g., calling a newly deployed or infrequently used program). The attack primitive (deploying many programs to inflate cache size, then repeatedly forcing cache misses) requires no elevated privilege, no validator/peer trust assumption, and no cooperation from other nodes — it is purely a function of permissionless program deployment plus ordinary transaction submission. The severity is bounded somewhat by the 90%-shrink target and by the fact that `unload_program_entry` only clears bytecode rather than removing keys (so `entries` map size, and thus flatten cost, is monotonically non-decreasing over the life of the validator process for live forks), which increases likelihood of the cost becoming material over time as more programs are deployed cluster-wide.

## Recommendation
- Avoid materializing a full flattened `Vec` of the entire program cache on every eviction call; maintain an incrementally-updated, bounded-size candidate/LRU structure (similar to the sampled `read_only_accounts_cache` eviction which explicitly avoids full-cache scans/`len()` calls for cost reasons) [5](#0-4) .
- Bound the total number of tracked keys in `IndexImplementation::V1 { entries, .. }` (not just unload bytecode) so that map/flatten costs cannot grow unbounded with the number of permissionlessly-deployed programs.
- Rate-limit or throttle how often `evict_using_random_selection`/`sort_and_unload` runs relative to program-cache growth, and/or move the flatten+sample cost off the transaction-processing critical path.

## Proof of Concept
Conceptual, since local static analysis cannot execute the validator:
1. Deploy a large number (N) of distinct BPF programs on-chain (permissionless, unprivileged; cost scales with rent, not with N in a prohibitive way).
2. Submit transactions from many different accounts that each invoke a program not currently warm in the per-batch cache (e.g., round-robin through the N deployed programs, or simply invoke a program shortly after another batch evicted it), forcing `program_cache_for_tx_batch.loaded_missing = true` on many concurrent/successive batches.
3. Each such batch calls `evict_using_random_selection`, which calls `get_flattened_entries()` [1](#0-0)  and iterates/samples over all N cached entries while holding `global_program_cache.write()` [4](#0-3) , so as N grows the per-batch latency imposed on unrelated concurrent transaction batches grows correspondingly, degrading throughput for all users — the same "unbounded loop reachable by any user, blocking others' mint/redeem/swap" pattern described in the source report.

**Caveat:** I was not able to fully verify (within the tool budget) the exact cap value referenced by `percent_of_max_entries`/`MAX_LOADED_ENTRY_COUNT`, nor the precise growth-rate limits on `IndexImplementation::V1.entries` over long validator uptimes, nor whether `prune()`/rerooting removes stale program keys aggressively enough in practice to keep this bounded in real-world operation. A Devin session with full repository access should inspect `program-runtime/src/loaded_programs.rs` (`percent_of_max_entries`, `MAX_LOADED_ENTRY_COUNT`, `remove_programs_with_no_entries`) and `svm/src/transaction_processor.rs` (`loaded_missing`/`merged_modified` setters) to confirm whether existing pruning fully bounds this cost or whether it is indeed unbounded in the way described above.

### Citations

**File:** program-runtime/src/loaded_programs.rs (L233-259)
```rust
/// This structure is the global cache of loaded, verified and compiled programs.
///
/// It ...
/// - is validator global and fork graph aware, so it can optimize the commonalities across banks.
/// - handles the visibility rules of un/re/deployments.
/// - stores the usage statistics and verification status of each program.
/// - is elastic and uses a probabilistic eviction strategy based on the usage statistics.
/// - also keeps the compiled executables around, but only for the most used programs.
/// - supports various kinds of tombstones to avoid loading programs which can not be loaded.
/// - cleans up entries on orphan branches when the block store is rerooted.
/// - supports the cache preparation phase before feature activations which can change cached programs.
/// - manages the environments of the programs and upcoming environments for the next epoch.
/// - allows for cooperative loading of TX batches which hit the same missing programs simultaneously.
/// - enforces that all programs used in a batch are eagerly loaded ahead of execution.
/// - is not persisted to disk or a snapshot, so it needs to cold start and warm up first.
pub struct ProgramCache<FG: ForkGraph> {
    /// Index of the cached entries and cooperative loading tasks
    pub(crate) index: IndexImplementation,
    /// The slot of the last rerooting
    pub latest_root_slot: Slot,
    /// Statistics counters
    pub stats: ProgramCacheStats,
    /// Reference to the block store
    pub fork_graph: Option<Weak<RwLock<FG>>>,
    /// Coordinates TX batches waiting for others to complete their task during cooperative loading
    pub loading_task_waiter: Arc<LoadingTaskWaiter>,
}
```

**File:** program-runtime/src/loaded_programs.rs (L822-837)
```rust
    /// Returns the list of entries which are verified and compiled.
    pub fn get_flattened_entries(&self) -> Vec<(Pubkey, Slot, Arc<ProgramCacheEntry>)> {
        match &self.index {
            IndexImplementation::V1 { entries, .. } => entries
                .iter()
                .flat_map(|(id, second_level)| {
                    second_level
                        .iter()
                        .filter_map(move |program| match program.program {
                            ProgramCacheEntryType::Loaded(_) => Some((*id, 0, program.clone())),
                            _ => None,
                        })
                })
                .collect(),
        }
    }
```

**File:** program-runtime/src/loaded_programs.rs (L862-930)
```rust
    /// Unloads programs which were used infrequently
    pub fn sort_and_unload(&mut self, shrink_to_percent: Percent) {
        let mut sorted_candidates = self.get_flattened_entries();
        sorted_candidates.sort_by_cached_key(|(_id, _last_modification_slot, program)| {
            program.stats.uses.load(Ordering::Relaxed)
        });
        let num_to_unload = sorted_candidates
            .len()
            .saturating_sub(percent_of_max_entries(shrink_to_percent));
        for (program, last_modification_slot, entry) in sorted_candidates.iter().take(num_to_unload)
        {
            self.unload_program_entry(*program, *last_modification_slot, entry);
        }
    }

    /// Evicts programs using random selection, choosing the worst scoring program out of the
    /// entries sampled.
    ///
    /// The eviction is performed enough number of times to reduce the cache usage to the given
    /// percentage.
    pub fn evict_using_random_selection(&mut self, shrink_to_percent: Percent, now: Slot) {
        let mut candidates = self.get_flattened_entries();
        let mut rng = rng();
        self.stats
            .water_level
            .store(candidates.len() as u64, Ordering::Relaxed);
        let num_to_unload = candidates
            .len()
            .saturating_sub(percent_of_max_entries(shrink_to_percent));
        let mut sample_entry = |candidates: &Vec<(Pubkey, u64, Arc<ProgramCacheEntry>)>| {
            // gen_range is deprecated in favor of random_range in rand>=0.9, but we also get
            // rnd() from shuttle, which doesn't yet support rand 0.9 APIs
            #[cfg(feature = "shuttle-test")]
            let index = rng.gen_range(0..candidates.len());
            #[cfg(not(feature = "shuttle-test"))]
            let index = rng.random_range(0..candidates.len());
            let usage_counter = candidates
                .get(index)
                .expect("Failed to get cached entry")
                .2
                .retention_score();
            (index, usage_counter)
        };

        // Random sampling with just 2 choices can frequently lead to a situation where both
        // entries chosen have relatively high retention scores, having us to pick one out of two
        // poor options. We can tell what a relatively high retention score is, so we can make a
        // few additional samples until we hit some other entry that isn't as highly scoring.
        //
        // Note that the "high enough" compilation time and use count numbers used here are
        // relatively arbitrary.
        const MAX_ADDITIONAL_SAMPLES: usize = 3;
        let avoid_evicting_above_score = retention_score(now, 500 * EMA_SCALE, 500);
        for _ in 0..num_to_unload {
            let (mut index, mut score) = sample_entry(&candidates);
            for _ in 0..MAX_ADDITIONAL_SAMPLES {
                let (sample_index, sample_score) = sample_entry(&candidates);
                if score > sample_score {
                    index = sample_index;
                    score = sample_score;
                }
                if score < avoid_evicting_above_score {
                    break;
                }
            }
            let (id, last_modification_slot, entry) = candidates.swap_remove(index);
            self.unload_program_entry(id, last_modification_slot, &entry);
        }
    }
```

**File:** svm/src/transaction_processor.rs (L660-671)
```rust
        // Skip eviction when there's no chance this particular tx batch has increased the size of
        // ProgramCache entries. Note that loaded_missing is deliberately defined, so that there's
        // still at least one other batch, which will evict the program cache, even after the
        // occurrences of cooperative loading.
        if program_cache_for_tx_batch.loaded_missing || program_cache_for_tx_batch.merged_modified {
            // NOTE: this is a percentage; do not set above 100.
            const SHRINK_LOADED_PROGRAMS_TO_PERCENTAGE: Percent = 90;
            self.global_program_cache
                .write()
                .unwrap()
                .evict_using_random_selection(SHRINK_LOADED_PROGRAMS_TO_PERCENTAGE, self.slot);
        }
```

**File:** accounts-db/src/read_only_accounts_cache.rs (L371-397)
```rust
        while data_size.load(Ordering::Relaxed) > target_data_size {
            let mut key_to_evict = None;
            let mut min_update_time = u64::MAX;
            let mut remaining_samples = evict_sample_size;
            // NOTE: This can loop indefinitely if the cache is misconfigured
            // and when we get here there aren't at least `evict_sample_size`
            // elements. We could break the loop on `cache.is_empty()` but
            // calling `is_empty()` and `len()` on a dashmap is very expensive
            // as it requires iterating and locking all the shards. So, avoid
            // paying that cost and assume that when eviction triggers the
            // cache contains enough items.
            while remaining_samples > 0 {
                let shard = cache
                    .shards()
                    .choose(rng)
                    .expect("number of shards should be greater than zero");
                let shard = shard.read();
                for (key, entry) in shard.iter().choose_multiple(rng, remaining_samples) {
                    let last_update_time = entry.get().last_update_time.load(Ordering::Relaxed);
                    if last_update_time < min_update_time {
                        min_update_time = last_update_time;
                        key_to_evict = Some(key.to_owned());
                    }

                    remaining_samples = remaining_samples.saturating_sub(1);
                }
            }
```

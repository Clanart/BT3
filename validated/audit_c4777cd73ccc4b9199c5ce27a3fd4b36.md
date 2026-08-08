The bug-class here — an ever-growing, cheaply-inflatable on-chain/host data structure that must be fully scanned by unrelated future operations — has a concrete analog in the agave `ProgramCache`.

### Title
Denial of Service via Unbounded Growth of the Global `ProgramCache` Tombstone/Unloaded Entries - (File: `program-runtime/src/loaded_programs.rs`)

### Summary
`ProgramCache` maintains a global `entries: HashMap<Pubkey, Vec<Arc<ProgramCacheEntry>>>` that any unprivileged user can cheaply grow without bound by referencing many distinct accounts owned by a BPF loader as transaction account keys. Entries that fail verification become permanent "tombstones" that are only removed by `prune()` when overshadowed on a rooted fork or orphaned — otherwise they persist forever. Every subsequent transaction batch that loads any new program triggers cache eviction routines that flatten and iterate the *entire* `entries` map, so the attacker-inflated map size directly and permanently increases the per-batch cost paid by every future, unrelated transaction on every validator, mirroring the SHToken `users` array DoS pattern.

### Finding Description
`filter_executable_program_accounts()` scans every account key referenced by a transaction (not just program invocation targets) and, for any account owned by `bpf_loader`, `bpf_loader_deprecated`, `bpf_loader_upgradeable`, or `loader_v4`, schedules it to be loaded into the `ProgramCache`: [1](#0-0) 

`load_program_with_pubkey()` then attempts to parse the account as a program; if the data is invalid it produces a `FailedVerification` tombstone which is inserted permanently into the cache: [2](#0-1) 

Tombstones (`FailedVerification`, `Closed`, `DelayVisibility`) and `Unloaded` entries are explicitly *never evicted* by the probabilistic eviction routines: [3](#0-2) 

They are only removed by `prune()` "when on orphan fork or overshadowed on the rooted fork": [4](#0-3) 

Since a garbage account's pubkey is never legitimately redeployed to "overshadow" itself, once its slot is rooted the tombstone remains in `entries` indefinitely. Yet every ordinary transaction batch that loads a missing/modified program calls `evict_using_random_selection()`: [5](#0-4) 

which (like `sort_and_unload()`) first calls `get_flattened_entries()`, an O(N) scan across **all** keys and **all** versions in the global map, regardless of whether those entries are eventually filtered out: [6](#0-5) [7](#0-6) 

This scan runs entirely on the host side, outside SBF compute metering, so its cost is not charged to any transaction's compute budget — a materially underpriced, unbounded-growth operation exactly analogous to `deleteUserFromArray()` iterating the unbounded `users` array in the SHToken report.

### Impact Explanation
An attacker can cheaply create many accounts owned by a BPF loader (rent-exempt minimum, largely refundable by later closing them) and reference each as an account key across many low-fee transactions. Each reference permanently inserts a tombstone entry into the validator-global `ProgramCache`. As this map grows, `get_flattened_entries()` — invoked from `evict_using_random_selection()`/`sort_and_unload()` on essentially every transaction batch that loads a new or modified program — takes proportionally longer on *every* validator (leader and all replaying followers), degrading block production and replay performance for the entire cluster, independent of and outside any per-transaction compute budget accounting.

### Likelihood Explanation
The attack path requires no special privileges: creating loader-owned accounts with invalid data and referencing them in transactions is available to any funded account. The number of entries an attacker can add is bounded only by economic cost (rent + fees), not by any protocol-enforced cap on `ProgramCache` size for tombstone/unloaded entries, making sustained, incremental growth of the cache straightforward over time.

### Recommendation
Bound the total number of tracked tombstone/unloaded entries in `ProgramCache` (e.g., cap per-key history or globally, and evict/prune based on staleness independent of fork overshadowing), and/or make `get_flattened_entries()`/eviction cost scale only with the subset of entries actually eligible for eviction rather than the full map. Consider charging compute or rent-related cost proportional to the number of distinct loader-owned accounts a transaction newly introduces to the cache.

### Proof of Concept
1. Create `N` accounts, each with `owner` set to `bpf_loader::id()` (or another loader ID) and non-program (invalid) data — cost is the rent-exempt minimum, refundable later.
2. Submit `N` transactions (or batch several per transaction) that include each new account as an account key (it does not need to be the instruction's program id) — this causes `filter_executable_program_accounts()` → `replenish_program_cache()` → `load_program_with_pubkey()` to insert a `FailedVerification` tombstone per pubkey into the global `ProgramCache`.
3. Once these deployment slots are rooted, the tombstones are permanent (`prune()` only removes overshadowed/orphaned entries).
4. Observe that `evict_using_random_selection()` (triggered on subsequent unrelated transaction batches that load any missing program) takes increasingly longer as `entries.len()` grows, because `get_flattened_entries()` iterates the entire map each time — reproducing, at the validator level, the same unbounded-iteration cost growth pattern demonstrated by the SHToken `testFail_ArrayOverflowDoS` PoC.

### Citations

**File:** svm/src/program_loader.rs (L180-193)
```rust
    .unwrap_or_else(|(deployment_slot, owner)| {
        let env = ProgramRuntimeEnvironment::clone(program_runtime_environment);
        ProgramCacheEntry::new_tombstone(
            deployment_slot,
            owner,
            ProgramCacheEntryType::FailedVerification(env),
        )
    });

    #[cfg(feature = "metrics")]
    load_program_metrics.submit_datapoint(&mut execute_timings.details);
    loaded_program.update_access_slot(current_slot);
    Some((Arc::new(loaded_program), last_modification_slot))
}
```

**File:** svm/src/program_loader.rs (L235-276)
```rust
pub fn filter_executable_program_accounts<'a, CB: TransactionProcessingCallback>(
    callbacks: &CB,
    program_cache_for_tx_batch: &ProgramCacheForTxBatch,
    keys: impl Iterator<Item = &'a Pubkey>,
    check_program_deployment_slot: bool,
) -> Vec<ProgramToLoad<'a>> {
    let mut result = Vec::new();
    for account_key in keys {
        if let Some(cache_entry) = program_cache_for_tx_batch.find(account_key) {
            cache_entry.stats.uses.fetch_add(1, Ordering::Relaxed);
        } else if let Some((account, last_modification_slot)) =
            callbacks.get_account_shared_data(account_key)
        {
            let loader = if loader_v4::check_id(account.owner()) {
                ProgramCacheEntryOwner::LoaderV4
            } else if bpf_loader_upgradeable::check_id(account.owner()) {
                ProgramCacheEntryOwner::LoaderV3
            } else if bpf_loader::check_id(account.owner()) {
                ProgramCacheEntryOwner::LoaderV2
            } else if bpf_loader_deprecated::check_id(account.owner()) {
                ProgramCacheEntryOwner::LoaderV1
            } else {
                continue;
            };
            let match_criteria = if check_program_deployment_slot {
                get_program_deployment_slot(callbacks, &account, loader)
                    .map_or(ProgramCacheMatchCriteria::Tombstone, |slot| {
                        ProgramCacheMatchCriteria::DeployedOnOrAfterSlot(slot)
                    })
            } else {
                ProgramCacheMatchCriteria::NoCriteria
            };
            result.push(ProgramToLoad {
                program_id: account_key,
                loader,
                match_criteria,
                last_modification_slot,
            });
        }
    }
    result
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

**File:** program-runtime/src/loaded_programs.rs (L860-875)
```rust
    }

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
```

**File:** program-runtime/src/loaded_programs.rs (L877-930)
```rust
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

**File:** program-runtime/src/program_cache_entry.rs (L94-96)
```rust
    Through pruning:
    - Closed / Unloaded / Loaded / Builtin => Empty in ProgramCache::prune (when on orphan fork or overshadowed on the rooted fork)
    - FailedVerification / Unloaded / Loaded => Unloaded in ProgramCache::prune (when on outdated program runtime environment)
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

Audit Report

## Title
Unbounded growth of `ProgramCache::entries` causes O(total-programs-ever-deployed) work on every rooted slot and epoch boundary - (File: `program-runtime/src/loaded_programs.rs`)

## Summary
`ProgramCache::prune()`, invoked from `Bank::prune_program_cache()` on new roots, iterates `entries.values_mut()` for every unique program `Pubkey` that has an entry in the cache, because the top-level `HashMap` keys are only removed via `remove_programs_with_no_entries()` when a program's entire version-history vector becomes empty [1](#0-0) [2](#0-1) . Since a normal, still-deployed program's entry is never emptied by `prune()`'s orphan-fork/environment filters, `entries.len()` grows roughly with the cumulative count of distinct programs ever deployed on the cluster, and deploying a unique BPF program is a cheap, unprivileged action.

## Finding Description
`ProgramCache::index` under `IndexImplementation::V1` is a `HashMap<Pubkey, Vec<Arc<ProgramCacheEntry>>>` keyed by every program id that has appeared in the cache [3](#0-2) . `prune()` walks `entries.values_mut()` in full, filtering each program's version vector for orphaned forks and outdated environments, and at the end calls `self.remove_programs_with_no_entries()`, which only deletes a key if its vector is now completely empty [4](#0-3) [2](#0-1) . For a program that remains deployed (the common case), its vector always retains at least the current-root entry, so its key is never reclaimed, and the top-level iteration cost of `prune()` is `O(entries.len())` regardless of how many programs are actually "live"/active in recent transactions. `get_flattened_entries()`, used by `prepare_program_cache_for_upcoming_feature_set` at epoch boundaries, similarly flattens and sorts the entire `entries` map [5](#0-4) [6](#0-5) . `Bank::prune_program_cache` is the caller of `ProgramCache::prune` [7](#0-6) ; I could not, within the tool budget, definitively confirm from `bank_forks.rs`/`blockstore_processor.rs`/`replay_stage.rs` whether this is invoked strictly once per root or is otherwise rate-limited/batched — the report itself flags this as unverified.

Deploying a BPF program via `bpf_loader_upgradeable` only costs rent-exempt lamports and is available to any unprivileged account, so an attacker can permissionlessly create an unbounded number of distinct top-level keys in `entries`.

## Impact Explanation
This is a genuine algorithmic scaling issue in a validator-internal hot path (per-root maintenance and epoch-boundary preparation), and it degrades performance proportional to cumulative historical program count rather than active working-set size. However, the concrete performance cost per entry in `prune()` is a cheap filter/retain over what is typically a short version-history vector per program, not an expensive operation, and no reroot-frequency/cost-budget data was verified to confirm this translates into missed leader slots or a validator crash/halt. This is a resource-bound/performance-degradation concern rather than a fund-loss, false-execution, false-rooting, or consensus-halt bug, and its magnitude (real-world scaling into a "non-RPC remote exhaustion/crash") is not empirically demonstrated — the total number of distinct programs ever deployed on a live mainnet-scale cluster is bounded by real-world deployment costs (rent-exempt minimum per program account, transaction fees), which constrains how "unbounded" this growth practically is compared to Bond's essentially free market-creation loop.

## Likelihood Explanation
An unprivileged attacker can deploy many distinct trivial programs at rent-exempt cost, which is qualitatively similar to the Bond Protocol's cheap market-creation primitive. But unlike Bond's `marketCounter` loop (invoked on every external view/interaction call with a strictly linear, unamortized cost that could revert entire transactions), `prune()`'s cost here is spread over per-root maintenance already performed by the validator regardless of attacker activity, and each additional key adds only marginal, cheap work. No PoC measurement of actual wall-clock scaling under realistic deployment volumes was performed or found in the codebase to substantiate a crash/exhaustion outcome rather than gradual, bounded-by-real-cost degradation.

## Recommendation
If confirmed via profiling that `prune()`/`get_flattened_entries()` cost scales problematically with total historical program count, consider garbage-collecting stale/closed-program keys more proactively, amortizing the prune walk across multiple roots, or bounding `entries` size with an LRU/expiry policy keyed on program activity rather than program existence.

## Proof of Concept
Not independently verified: this analysis is based on static code review only. To substantiate real-world impact, one would need to (1) deploy a large number of distinct minimal BPF programs from unprivileged accounts, (2) measure `entries.len()` growth in `ProgramCache`, and (3) profile `Bank::prune_program_cache`/`ProgramCache::prune` and `get_flattened_entries()` wall-clock cost per root/epoch as program count scales, to determine whether the growth is severe enough to cause validator-observable degradation, missed slots, or crash — none of which was measured here.

### Citations

**File:** program-runtime/src/loaded_programs.rs (L248-259)
```rust
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

**File:** program-runtime/src/loaded_programs.rs (L511-519)
```rust
    pub fn prune(
        &mut self,
        new_root_slot: Slot,
        new_environment: Option<ProgramRuntimeEnvironment>,
        fork_graph: &FG,
    ) {
        match &mut self.index {
            IndexImplementation::V1 { entries, .. } => {
                for second_level in entries.values_mut() {
```

**File:** program-runtime/src/loaded_programs.rs (L600-612)
```rust
                        second_level.retain(|_entry| {
                            let retain_flag = *retain_flags.get(index_in_second_level).unwrap();
                            index_in_second_level = index_in_second_level.saturating_add(1);
                            retain_flag
                        });
                    }
                }
            }
        }
        self.remove_programs_with_no_entries();
        debug_assert!(self.latest_root_slot <= new_root_slot);
        self.latest_root_slot = new_root_slot;
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

**File:** program-runtime/src/loaded_programs.rs (L977-990)
```rust
    fn remove_programs_with_no_entries(&mut self) {
        match &mut self.index {
            IndexImplementation::V1 { entries, .. } => {
                let num_programs_before_removal = entries.len();
                entries.retain(|_key, second_level| !second_level.is_empty());
                if entries.len() < num_programs_before_removal {
                    self.stats.empty_entries.fetch_add(
                        num_programs_before_removal.saturating_sub(entries.len()) as u64,
                        Ordering::Relaxed,
                    );
                }
            }
        }
    }
```

**File:** runtime/src/bank.rs (L1660-1672)
```rust
                let program_cache_guard = self
                    .transaction_processor
                    .global_program_cache
                    .read()
                    .unwrap();
                epoch_boundary_preparation.programs_to_recompile = program_cache_guard
                    .get_flattened_entries()
                    .into_iter()
                    .map(|(id, _last_modification_slot, entry)| (id, entry))
                    .collect();
                epoch_boundary_preparation
                    .programs_to_recompile
                    .sort_by_cached_key(|(_id, program)| program.retention_score());
```

**File:** runtime/src/bank.rs (L1681-1701)
```rust
    pub fn prune_program_cache(&self, bank_forks: &BankForks) {
        let upcoming_environment = self
            .transaction_processor
            .epoch_boundary_preparation
            .write()
            .unwrap()
            .reroot(self.epoch());
        self.transaction_processor
            .global_program_cache
            .write()
            .unwrap()
            .prune(
                self.slot(),
                upcoming_environment.map(|_| {
                    ProgramRuntimeEnvironment::clone(
                        &self.transaction_processor.program_runtime_environment,
                    )
                }),
                bank_forks,
            );
    }
```

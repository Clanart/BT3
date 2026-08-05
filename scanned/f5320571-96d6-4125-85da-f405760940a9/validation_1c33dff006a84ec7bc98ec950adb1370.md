## Title
Global program-cache eviction limit can be hit by unprivileged, fee-paying deployers, forcing banking-stage to reject an entire, unrelated transaction batch — analogous to a shared quota that cannot be bypassed by "splitting" the load - ([File: svm/src/transaction_processor.rs])

## Summary
The zkSync/HubPool bug is a class of "shared cumulative quota" bug: the protocol assumed a limit could always be worked around by splitting a large operation into many small ones, but the limit is actually tracked on a global/cumulative counter, so once it is hit, *no* further operation of that kind can proceed, and this blocks an unrelated critical path (bundle execution) rather than just the offending caller. In Agave, the closest analog is the global `ProgramCache`'s `hit_max_limit` mechanism: when the shared, validator-global program cache is exhausted, `TransactionBatchProcessor::replenish_program_cache` sets `program_cache_for_tx_batch.hit_max_limit = true` [1](#0-0) , and the caller then fails **every transaction in the whole batch** with `TransactionError::ProgramCacheHitMaxLimit`, regardless of whether those transactions actually touch the programs that filled the cache [2](#0-1) .

## Finding Description
`ProgramCache` is a single, validator-global, cross-batch resource — it is explicitly documented as "validator global and fork graph aware" [3](#0-2) . Any unprivileged user can grow it merely by invoking (or deploying) programs that need to be loaded into the cache; loading is driven by `replenish_program_cache`, which is called for every transaction batch that references a program not already resident [4](#0-3) .

When the global cache assignment fails (i.e., `finish_cooperative_loading_task` returns `true`, indicating the cache slot could not be assigned) and `limit_to_load_programs` is set, the code does not fail only the offending transaction — it wipes the whole per-batch cache view and flags the **entire batch** as having hit the limit:

```
*program_cache_for_tx_batch = ProgramCacheForTxBatch::new(self.slot);
program_cache_for_tx_batch.hit_max_limit = true;
return;
``` [5](#0-4) 

Back in `load_and_execute_sanitized_transactions`, once `hit_max_limit` is observed, *every* transaction index in the batch — including transactions that never touched the program that caused the overflow — is unconditionally converted into an error and the whole batch execution short-circuits:

```
if program_cache_for_tx_batch.hit_max_limit {
    return LoadAndExecuteSanitizedTransactionsOutput {
        error_metrics,
        execute_timings,
        processing_results: (0..sanitized_txs.len())
            .map(|_| Err(TransactionError::ProgramCacheHitMaxLimit))
            .collect(),
        balance_collector: None,
    };
}
``` [2](#0-1) 

This is structurally the same broken invariant as the zkSync report: the protocol assumes the correct behavior is to isolate the failure to the transaction that overruns the limit ("split into smaller chunks and it'll work"), but the actual mechanism is a global counter/eviction budget shared across the whole batch (and, because the cache is validator-global, effectively shared across many batches/threads), so once saturated it takes down *unrelated* work in the same batch rather than only the transaction that filled it. No per-transaction guard exists to prevent an attacker's transaction from being the one that pushes a concurrently-processed batch over the edge; the check happens only after `replenish_program_cache` has already attempted (and failed) the assignment for the whole batch.

## Impact Explanation
If this global cache-exhaustion condition can be triggered cheaply and repeatedly by an unprivileged party (e.g., by deploying/invoking enough distinct large BPF programs concurrently across many parallel banking-stage threads sharing the same `global_program_cache`), it causes banking-stage worker threads to discard entire co-scheduled batches of otherwise valid, unrelated transactions via `TransactionError::ProgramCacheHitMaxLimit`. This is a non-RPC, remote, low-privilege-triggerable degradation of block production throughput/latency — legitimate users' transactions get dropped/retried en masse even though they have nothing to do with the programs that exhausted the cache, similar to how HubPool's unrelated bundle executions stopped working once the zkSync bridge quota was hit.

## Likelihood Explanation
Uncertain/moderate. I was not able to fully verify, within the available tool budget, (a) the exact capacity of the global `ProgramCache` and how easily it can be exhausted by a single unprivileged actor within one slot, (b) whether `limit_to_load_programs` is actually enabled during normal leader block production (`core/src/banking_stage/consumer.rs` references it, but I could not confirm the call site's value in this session), and (c) whether eviction/statistics-based reclamation in `program-runtime/src/loaded_programs.rs` (referenced as "probabilistic eviction strategy based on usage statistics") makes sustained exhaustion impractical in practice. These are exactly the kind of guards that could mitigate the "cannot bypass by splitting" problem, and I could not confirm their effectiveness with certainty.

## Recommendation
- Confirm the conditions under which `limit_to_load_programs` is `true` in production leader flows (`core/src/banking_stage/consumer.rs`, `runtime/src/bank.rs`), and whether an unprivileged actor can realistically fill the global `ProgramCache` within a slot given eviction/fee costs.
- If confirmed exploitable, avoid failing the *entire* batch on `hit_max_limit`; instead fail only the transaction(s) whose program could not be assigned a cache slot, so unrelated transactions in the same batch are not dropped.
- Consider making the global program-cache capacity scale with actual memory pressure rather than a hard slot-assignment failure, and/or rate-limit/charge higher fees for program loads that contend for cache slots.

## Proof of Concept
Not independently reproduced in this session (read-only code analysis only). A concrete PoC would need to:
1. Confirm `limit_to_load_programs=true` in the real banking-stage call path via `core/src/banking_stage/consumer.rs` and `runtime/src/bank.rs`.
2. Submit enough concurrent transactions invoking many distinct, large, previously-uncached programs across worker threads sharing one `global_program_cache` to force `finish_cooperative_loading_task` to return `true` for a batch.
3. Observe that a concurrently-processed, unrelated batch on another thread also returns all-`ProgramCacheHitMaxLimit` via the code path shown above, demonstrating collateral transaction rejection outside attacker control.

Given the residual uncertainty above, this should be treated as a candidate finding requiring further live/dynamic verification rather than a fully confirmed vulnerability.

### Citations

**File:** svm/src/transaction_processor.rs (L563-574)
```rust
                    if program_cache_for_tx_batch.hit_max_limit {
                        return LoadAndExecuteSanitizedTransactionsOutput {
                            error_metrics,
                            execute_timings,
                            processing_results: (0..sanitized_txs.len())
                                .map(|_| Err(TransactionError::ProgramCacheHitMaxLimit))
                                .collect(),
                            // If we abort the batch and balance recording is enabled, no balances should be
                            // collected. If this is a leader thread, no batch will be committed.
                            balance_collector: None,
                        };
                    }
```

**File:** svm/src/transaction_processor.rs (L894-921)
```rust
    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    fn replenish_program_cache<CB: TransactionProcessingCallback>(
        &self,
        account_loader: &AccountLoader<CB>,
        mut missing_programs: Vec<ProgramToLoad>,
        program_runtime_environment_for_execution: &ProgramRuntimeEnvironment,
        program_cache_for_tx_batch: &mut ProgramCacheForTxBatch,
        execute_timings: &mut ExecuteTimings,
        limit_to_load_programs: bool,
        increment_usage_counter: bool,
    ) {
        if missing_programs.is_empty() {
            // Nothing to load, so skip the global cache and fork graph locks.
            // Program-cache hit/miss counters are unchanged for empty work.
            return;
        }
        let mut count_hits_and_misses = true;
        loop {
            // Lock the global cache.
            let global_program_cache = self.global_program_cache.read().unwrap();
            // Figure out which program needs to be loaded next.
            let program_to_load = global_program_cache.extract(
                &mut missing_programs,
                program_cache_for_tx_batch,
                program_runtime_environment_for_execution,
                increment_usage_counter,
                count_hits_and_misses,
            );
```

**File:** svm/src/transaction_processor.rs (L941-959)
```rust
            if let Some((key, program, last_modification_slot)) = program_to_store {
                program_cache_for_tx_batch.loaded_missing = true;
                let mut global_program_cache = self.global_program_cache.write().unwrap();
                // Submit our last completed loading task.
                if global_program_cache.finish_cooperative_loading_task(
                    program_runtime_environment_for_execution,
                    self.slot,
                    key,
                    last_modification_slot,
                    program,
                ) && limit_to_load_programs
                {
                    // This branch is taken when there is an error in assigning a program to a
                    // cache slot. It is not possible to mock this error for SVM unit
                    // tests purposes.
                    *program_cache_for_tx_batch = ProgramCacheForTxBatch::new(self.slot);
                    program_cache_for_tx_batch.hit_max_limit = true;
                    return;
                }
```

**File:** program-runtime/src/loaded_programs.rs (L233-247)
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
```

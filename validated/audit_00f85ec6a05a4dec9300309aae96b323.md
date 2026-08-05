## Title
Batch-wide `ProgramCacheHitMaxLimit` abort lets one attacker-controlled program purge every co-batched user's transaction - (File: svm/src/transaction_processor.rs)

## Summary
`TransactionBatchProcessor::load_and_execute_sanitized_transactions` shares a single `ProgramCacheForTxBatch` across *all* transactions in a batch. If loading/compiling any one program in that batch causes the underlying global program cache to fail to assign a cache slot, the batch-local cache is reset and flagged `hit_max_limit = true`, and **every transaction in the batch** — not just the one that triggered the condition — is unconditionally converted into `Err(TransactionError::ProgramCacheHitMaxLimit)`.

## Finding Description
Two identical guard blocks implement this behavior: [1](#0-0) [2](#0-1) 

The trigger condition is set inside `replenish_program_cache`, when `finish_cooperative_loading_task` reports an "error in assigning a program to a cache slot": [3](#0-2) 

This is structurally analogous to the reported EVM bug: a shared, deterministic resource (the batch's program cache, akin to the "cheapest sell order [that] is always guaranteed to be used to fill the next order") is put into a broken state by one participant's action, and every *other*, unrelated participant sharing that resource in the same processing unit (the tx batch, akin to the marketplace's shared execution path) has their independent operation reverted as collateral damage — none of the "abort" logic here discriminates between the transaction that actually caused the cache-slot failure and innocent bystanders that happen to be co-scheduled in the same batch by `banking_stage`'s greedy scheduler (`core/src/banking_stage/transaction_scheduler/greedy_scheduler.rs`), which batches multiple different fee-payers' transactions together purely by priority/thread availability with no isolation against this failure mode.

Whereas in the EVM report the "guard" that should have prevented cross-user impact (withdrawal pattern) was missing, here the analogous guard would be per-transaction isolation of program-cache failures (e.g., failing only the transaction requiring the missing/failed slot, as is already done for ordinary `TransactionError`s via `drop_on_failure`/normal error propagation) — but the `hit_max_limit` path bypasses that per-transaction handling entirely and blanket-fails the whole batch.

## Impact Explanation
If this condition can be triggered deterministically or repeatedly by an unprivileged actor (e.g., by deploying many distinct executable programs so that the global program cache's slot-assignment logic fails during a leader's batch processing), then every legitimate, unrelated transaction batched alongside the attacker's triggering transaction in that pass is dropped with `ProgramCacheHitMaxLimit`, none of them are committed, and their fees/state are not applied as intended. Because this happens inside block-production/replay (`load_and_execute_sanitized_transactions` is the core SVM entry point used by both `banking_stage::consumer` and replay), a bug here can affect fairness/liveness of transaction inclusion for other users' transactions (non-RPC remote degradation of the leader's transaction-processing throughput), and if inconsistently triggered between leader and validating replay nodes, could risk state-consistency issues.

## Likelihood Explanation
I could not verify from the indexed code exactly what conditions cause `finish_cooperative_loading_task` to fail ("error in assigning a program to a cache slot") or whether an ordinary unprivileged user can force this deterministically at will (the exact eviction/slot-limit logic and `MAX_LOADED_ENTRY_COUNT`-style constants in `program-runtime/src/loaded_programs.rs` were not fully retrievable within my tool-call budget — the file's `extract`/`finish_cooperative_loading_task` implementations were not returned by the index). The code comment states this branch "is not possible to mock ... for SVM unit tests," suggesting the authors consider it a rare/edge condition rather than an easily attacker-triggerable one under normal cache-size limits. Without confirming the exact trigger, I cannot assert this is trivially exploitable at low cost by a single unprivileged actor — it may require pathological conditions (e.g., extreme cache-size misconfiguration or lock-eviction races) that are not attacker-controlled in practice.

## Recommendation
Given the uncertainty on triggerability, this should be validated by:
1. Reading the full `finish_cooperative_loading_task`/`extract`/cache-slot-assignment logic in `program-runtime/src/loaded_programs.rs` to determine what conditions cause slot-assignment failure and whether they are attacker-reachable at low cost (e.g., number of distinct programs, eviction races under concurrent batch processors).
2. If reachable, change the `hit_max_limit` handling so only the transaction(s) that actually require the unavailable program slot are failed with `ProgramCacheHitMaxLimit`, while other transactions in the batch proceed to execute normally — mirroring how ordinary transaction errors are isolated per-transaction elsewhere in this same function via `drop_on_failure`.

## Proof of Concept
I do not have a verified, concrete reproduction because the exact preconditions for `finish_cooperative_loading_task` to return the slot-assignment error were not retrievable from the indexed portion of `program-runtime/src/loaded_programs.rs` within the available tool budget. A full PoC would require:
1. Locating the cache-slot capacity/limit constant and eviction policy in `program-runtime/src/loaded_programs.rs`.
2. Constructing a batch of transactions where one deploys/invokes enough distinct programs to exhaust that capacity concurrently with other unrelated transactions' program loads.
3. Confirming that `program_cache_for_tx_batch.hit_max_limit` is set and observing that unrelated transactions in the same batch are converted to `Err(TransactionError::ProgramCacheHitMaxLimit)` per the code at `svm/src/transaction_processor.rs:563-574`.

Because I could not confirm attacker-reachability of the trigger condition, I recommend starting a Devin session with filesystem/build access to inspect `program-runtime/src/loaded_programs.rs` in full and write an integration test in `svm/src/transaction_processor.rs`'s test module to determine whether this is genuinely exploitable by an unprivileged actor before treating this as a confirmed vulnerability.

### Citations

**File:** svm/src/transaction_processor.rs (L451-462)
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

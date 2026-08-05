### Title
Single transaction's program-cache assignment failure aborts an entire unrelated transaction batch with `ProgramCacheHitMaxLimit` - ([File: svm/src/transaction_processor.rs])

### Summary
`TransactionBatchProcessor::load_and_execute_sanitized_transactions` shares one `ProgramCacheForTxBatch` instance across every transaction in a batch. If `replenish_program_cache` fails to assign a loaded program into the global cache for *any single transaction* in the batch, the entire local cache is discarded and `hit_max_limit` is set, which causes the processor to immediately return `TransactionError::ProgramCacheHitMaxLimit` for *every* transaction in the batch — including ones that had nothing to do with the offending program. This is structurally the same "one bad participant bricks all co-scheduled participants sharing a resource" pattern described in the Perennial `KeeperOracle.commit`/`Market.update` report, where a single paused/reverting `Market` blocked settlement for every other market sharing the same oracle.

### Finding Description
`ProgramCache::extract`/`finish_cooperative_loading_task` cooperatively loads missing programs for a batch of transactions into a batch-local `ProgramCacheForTxBatch`. When `finish_cooperative_loading_task` reports that assigning an entry into a cache slot failed (and `limit_to_load_programs` is set), the code discards *all* entries collected so far for the whole batch and flags `hit_max_limit = true`: [1](#0-0) 

Both call sites that check this flag propagate the failure to the *entire* batch rather than just the transaction that needed the missing program: [2](#0-1) [3](#0-2) 

The `ProgramCacheForTxBatch` and its `hit_max_limit` flag are explicitly documented as shared, batch-wide state, not per-transaction state: [4](#0-3) 

So a program-loading race/assignment failure triggered while resolving *one* transaction's missing program (e.g., a newly-deployed or redeployed program referenced by only that transaction) discards the cache and error-codes every other transaction in the batch — transactions that may reference completely unrelated programs and accounts, submitted by unrelated users. This mirrors the Perennial bug's broken invariant: a shared callback/queue structure iterated for multiple independent consumers, where one consumer's failure aborts processing for all consumers, rather than isolating the failure to the offending participant.

### Impact Explanation
If triggered, this converts a localized, single-transaction condition (failure to insert a program into the shared cache) into a batch-wide denial of service: every transaction that happens to be scheduled/batched alongside the failing one is rejected with `ProgramCacheHitMaxLimit`, even though their execution had no dependency on the problematic program. Depending on how large batches are (banking-stage batches / SVM integration-test batches / entry batches during replay), this can degrade throughput or cause otherwise-valid, unrelated transactions to fail non-deterministically depending on batch composition, which is itself a fairness/availability concern for unprivileged users whose transactions get bundled with an unlucky one.

### Likelihood Explanation
The comment at the assignment-failure site states this error path "is not possible to mock ... for SVM unit tests purposes," indicating the exact trigger conditions for `finish_cooperative_loading_task` returning `true` are rare/edge-case (racing cooperative loaders, or an internal invariant violation in cache-slot assignment) and not fully exercised by existing tests. I could not fully trace all conditions under which `finish_cooperative_loading_task` signals this failure within the time available — this analysis is based on the two confirmed batch-abort call sites and the documented sharing of `ProgramCacheForTxBatch`, but the exact attacker-triggerable precondition for the assignment failure itself is unverified from local code alone.

### Recommendation
Scope the effect of a program-cache assignment failure to the specific transaction that needed the missing program (fail only that transaction with `ProgramCacheHitMaxLimit`/an appropriate error) instead of discarding the shared `ProgramCacheForTxBatch` and failing the whole batch. If the batch-wide invalidation is intentional/required by the cache's invariants, document why it cannot be scoped per-transaction and confirm this is not attacker-triggerable at will (e.g., via crafted redeploy/close/redeploy sequences racing with normal user transactions in the same batch).

### Proof of Concept
Could not be constructed with local, read-only investigation. The two exact code paths that turn a single-transaction cache event into a whole-batch failure are demonstrated above; reproducing the triggering condition for `finish_cooperative_loading_task`'s failure branch would require running the cooperative-loading code under concurrent/multi-threaded transaction-processor execution (multiple `TransactionBatchProcessor` threads racing to load the same/different programs), which is out of scope for static code review.

### Citations

**File:** program-runtime/src/loaded_programs.rs (L271-287)
```rust
/// Local view into [ProgramCache] which was extracted for a specific TX batch.
///
/// This isolation enables the global [ProgramCache] to continue to evolve (e.g. evictions),
/// while the TX batch is guaranteed it will continue to find all the programs it requires.
/// For program management instructions this also buffers them before they are merged back into the global [ProgramCache].
#[derive(Clone, Debug, Default)]
pub struct ProgramCacheForTxBatch {
    /// Pubkey is the address of a program.
    /// ProgramCacheEntry is the corresponding program entry valid for the slot in which a transaction is being executed.
    entries: HashMap<Pubkey, Arc<ProgramCacheEntry>>,
    /// Program entries modified during the transaction batch.
    modified_entries: HashMap<Pubkey, Arc<ProgramCacheEntry>>,
    slot: Slot,
    pub hit_max_limit: bool,
    pub loaded_missing: bool,
    pub merged_modified: bool,
}
```

**File:** program-runtime/src/loaded_programs.rs (L941-959)
```rust
    }

    /// This function removes the given entry for the given program from the cache.
    /// The function expects that the program and entry exists in the cache. Otherwise it'll panic.
    fn unload_program_entry(
        &mut self,
        id: Pubkey,
        _last_modification_slot: Slot,
        remove_entry: &Arc<ProgramCacheEntry>,
    ) {
        match &mut self.index {
            IndexImplementation::V1 { entries, .. } => {
                let second_level = entries.get_mut(&id).expect("Cache lookup failed");
                let candidate = second_level
                    .iter_mut()
                    .find(|entry| Arc::ptr_eq(entry, remove_entry))
                    .expect("Program entry not found");

                // Only loaded entries shall be unloaded by eviction.
```

**File:** svm/src/transaction_processor.rs (L446-462)
```rust
        // Clone the batch-local program cache (builtins already populated in new_from()).
        // User-deployed programs are loaded per-transaction via replenish_program_cache
        // in the transaction loop below.
        let mut program_cache_for_tx_batch = self.builtin_program_cache.read().unwrap().clone();

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

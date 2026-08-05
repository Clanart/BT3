Confirmed: the unified scheduler's `execute_batch` in `runtime/src/transaction_execution.rs` calls `execute_batch` (the same code path shown above) which executes and commits transactions to the bank via `load_execute_and_commit_transactions_with_pre_commit_callback` *before* `check_block_cost_limits` runs, per batch/entry granularity, not per-block. This confirms the "execute-then-check" ordering used both in banking-stage-style processing and in the unified-scheduler replay path.

### Title
Block cost limit is enforced only after full transaction execution/commit, letting a heavy block consume unbounded validator CPU before being rejected as dead - (File: `runtime/src/transaction_execution.rs`)

### Summary
Agave's block-level compute-unit budget (`MAX_BLOCK_UNITS`, `cost-model/src/block_cost_limits.rs`) is meant to bound how much work a block can require, analogous to a block gas limit. However, the enforcement point in `execute_batch` (`runtime/src/transaction_execution.rs:57-169`) runs the cost check with `check_block_cost_limits` (line 92) only *after* `load_execute_and_commit_transactions_with_pre_commit_callback` has already fully executed and committed every transaction in the batch (line 79-87). Because entries/batches are scheduled and executed as a unit (see `process_entries` in `ledger/src/blockstore_processor.rs:205-251`, which schedules all transactions in an entry onto the bank's scheduler without any prior per-entry cost gate), a validator replaying a block must pay the full real CPU/wall-clock cost of executing an entry's transactions before the aggregate cost tracker (`cost-model/src/cost_tracker.rs`) can detect and reject an over-limit block. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`execute_batch` (`runtime/src/transaction_execution.rs:57`) is the shared entry point used by both the legacy processing path and the unified scheduler (`unified-scheduler-pool/src/lib.rs:1-11` explicitly documents that it "commits any side-effects... via `execute_batch`"). The function's control flow is: (1) fully execute and commit the batch's transactions via SVM, (2) *then* compute actual per-transaction costs from the commit results (`get_transaction_costs`, lines 171-195), and (3) feed those costs into `check_block_cost_limits` (lines 157-169), which calls `CostTracker::try_add`/`would_fit` (`cost-model/src/cost_tracker.rs:167-310`). If the cumulative cost exceeds `MAX_BLOCK_UNITS` or per-account limits, `checked_tx_costs_result?` (line 100) propagates a `TransactionError`, which bubbles up through replay and results in the bank being marked dead (`core/src/replay_stage/dead_slots.rs`, `mark_replay_dead_slot`). [4](#0-3) 

The critical gap is that nothing prevents an entry/batch from containing a large amount of real execution work (many transactions, heavy compute, large loaded-account-data) whose *actual* executed cost only becomes known after the SVM has already run every transaction and applied state changes to the (soon-to-be-discarded) bank. Unlike the leader-side banking stage, where `try_add_processed_transaction_costs` (`core/src/banking_stage/consumer.rs:530-599`) also only adds costs post-execution but operates transaction-by-transaction with an `all_or_nothing`/`remaining_batch_error` rollback that stops as soon as the tracker rejects a transaction, the replay-side `process_entries` (`ledger/src/blockstore_processor.rs:205-251`) schedules and executes an *entire entry's* transactions at once with no pre-check against the cost tracker, and the cost validation for the unified-scheduler path happens per-entry, after execution, inside `execute_batch`. This means every honest replaying validator on the network is forced to pay the full real compute/IO cost of the entry before the violation is even detected. [5](#0-4) 

This is the direct analog of the reported ZkEvm issue: `finalizeBlocks` computes/validates proof and state after accepting block data, and if the actual gas required exceeds the block gas limit, the expensive work has already been attempted. Here, the "gas requirement" is the real compute-unit/account-load cost of a batch of transactions, and the "block gas limit" is `MAX_BLOCK_UNITS`; the check that is supposed to bound resource consumption is performed only after the resource has already been consumed. [6](#0-5) 

### Impact Explanation
Because the amount of "free" work a leader can force onto every replaying validator is bounded only by the physical size limits of a block (max shred/entry data), not by the cost-tracker's compute-unit budget, a leader can construct a block whose entries are packed with transactions that are individually valid (correct signatures, valid instructions) but whose real execution cost is deliberately large (e.g., maximizing `programs_execution_cost`, `loaded_accounts_data_size_cost`, and per-account write-lock costs), such that the aggregate cost only becomes detectably over `MAX_BLOCK_UNITS` near the very end of the block. Every validator replaying that block spends real CPU/time executing nearly a full block's worth of heavy transactions before the block is finally marked dead. Repeated over successive leader slots, this is a non-RPC, remote resource-exhaustion vector against the entire fleet of replaying validators — this differs from a trivial "malicious leader can produce a bad block" statement because the specific bug is that the enforcement mechanism intended to *bound* that leader's resource-consumption power (the cost tracker / `MAX_BLOCK_UNITS`) is checked too late to actually prevent the resource consumption, only the acceptance of the resulting bank.

### Likelihood Explanation
Any validator that becomes leader for a slot can construct such a block; no privileged access or additional compromise is required beyond normal leader-slot assignment, and the mechanism (`execute_batch`'s check-after-execute ordering) is present in the mainline replay/execution path for both the classic and unified-scheduler code paths. The severity is bounded by per-slot block size limits, so this is a repeated, throttleable-but-persistent CPU cost imposed on the cluster rather than an unbounded single-shot crash.

### Recommendation
Add a pre-execution (estimated) cost gate at the entry/batch granularity during replay — mirroring the estimate-then-reserve pattern already used in the banking stage's scheduler (`CostModel::estimate_cost`, `core/src/banking_stage/transaction_scheduler/greedy_scheduler.rs`) — so that entries whose *estimated* cost already exceeds the remaining block budget are rejected (and the block marked dead) before `load_execute_and_commit_transactions_with_pre_commit_callback` is invoked, rather than only checking with actual post-execution costs in `check_block_cost_limits`. This bounds the worst-case wasted CPU per rejected block to roughly one entry's estimated-vs-actual delta instead of an entire block.

### Proof of Concept
Conceptual PoC (not executable from indexed code alone, but grounded in the traced control flow):
1. As leader, build a block whose final PoH entry contains many transactions, each requesting the maximum `compute_unit_limit` and `loaded_accounts_data_size_limit` permitted by `ComputeBudget` instructions, and touching distinct writable accounts to also avoid tripping the smaller `MAX_WRITABLE_ACCOUNT_UNITS` limit early.
2. Ensure the cumulative *actual* executed cost of the block only exceeds `MAX_BLOCK_UNITS` (`cost-model/src/block_cost_limits.rs:26-27`) within the final entry.
3. Broadcast the block. Every replaying validator calls `process_entries`/`execute_batch` (`ledger/src/blockstore_processor.rs:205-251`, `runtime/src/transaction_execution.rs:57-100`), fully executing and committing all prior entries plus the final heavy entry's transactions via SVM, before `check_block_cost_limits` (line 92) detects the overflow and returns `Err(TransactionError::WouldExceedMaxBlockCostLimit)`, causing the bank to be marked dead.
4. Repeat every leader slot to sustain elevated CPU load across the validator set, all for blocks that are ultimately discarded. [7](#0-6) [8](#0-7)

### Citations

**File:** runtime/src/transaction_execution.rs (L57-100)
```rust
pub fn execute_batch<'a>(
    batch: &'a TransactionBatchWithIndexes<impl TransactionWithMeta>,
    bank: &'a Arc<Bank>,
    transaction_status_sender: Option<&'a TransactionStatusSender>,
    replay_vote_sender: Option<&'a ReplayVoteSender>,
    replay_vote_send_type: ReplayVoteSendType,
    timings: &'a mut ExecuteTimings,
    log_messages_bytes_limit: Option<usize>,
    prioritization_fee_cache: Option<&'a PrioritizationFeeCache>,
) -> TransactionResult<()> {
    let TransactionBatchWithIndexes {
        batch,
        transaction_indexes,
    } = batch;

    let transaction_indexes = Cow::from(transaction_indexes);

    let pre_commit_callback = |processing_results: &_| -> TransactionResult<()> {
        // We're entering into one of the block-verification methods.
        get_first_error(batch, processing_results)
    };

    let (commit_results, balance_collector) = batch
        .bank()
        .load_execute_and_commit_transactions_with_pre_commit_callback(
            batch,
            ExecutionRecordingConfig::new_single_setting(transaction_status_sender.is_some()),
            timings,
            log_messages_bytes_limit,
            pre_commit_callback,
        )?;

    let mut check_block_costs_elapsed = Measure::start("check_block_costs");

    let tx_costs = get_transaction_costs(bank, &commit_results, batch.sanitized_transactions());
    let checked_tx_costs_result = check_block_cost_limits(bank, &tx_costs);

    check_block_costs_elapsed.stop();
    timings.saturating_add_in_place(
        ExecuteTimingType::CheckBlockLimitsUs,
        check_block_costs_elapsed.as_us(),
    );

    checked_tx_costs_result?;
```

**File:** runtime/src/transaction_execution.rs (L157-169)
```rust
fn check_block_cost_limits<Tx: TransactionWithMeta>(
    bank: &Bank,
    tx_costs: &[Option<TransactionCost<'_, Tx>>],
) -> TransactionResult<()> {
    let mut cost_tracker = bank.write_cost_tracker().unwrap();
    for tx_cost in tx_costs.iter().flatten() {
        cost_tracker
            .try_add(tx_cost)
            .map_err(TransactionError::from)?;
    }

    Ok(())
}
```

**File:** ledger/src/blockstore_processor.rs (L205-251)
```rust
fn process_entries(bank: &BankWithScheduler, entries: Vec<ReplayEntry>) -> Result<()> {
    let mut tick_hashes = vec![];

    for ReplayEntry {
        entry,
        starting_index,
    } in entries
    {
        match entry {
            EntryType::Tick(hash) => {
                // If it's a tick, save it for later
                tick_hashes.push(hash);
                if bank.is_block_boundary(bank.tick_height() + tick_hashes.len() as u64) {
                    break;
                }
            }
            EntryType::Transactions(transactions) => {
                if transactions.is_empty() {
                    continue;
                }

                // Any bank replaying transactions must have a scheduler installed. Slot 0 -
                // the only bank replayed before the scheduler pool is installed - is tick-only,
                // so it never reaches here.
                assert!(
                    bank.has_installed_scheduler(),
                    "no scheduler installed for bank of slot {} during replay",
                    bank.slot()
                );
                validate_entry_transactions(
                    &transactions,
                    bank.get_transaction_account_lock_limit(),
                )?;

                let indexes = starting_index..starting_index + transactions.len();
                // Widening usize index to OrderedTaskId (= u128) won't ever fail.
                let task_ids = indexes.map(|i| i.try_into().unwrap());

                bank.schedule_transaction_executions(transactions.into_iter().zip_eq(task_ids))?;
            }
        }
    }
    for hash in tick_hashes {
        bank.register_tick(&hash);
    }
    Ok(())
}
```

**File:** cost-model/src/block_cost_limits.rs (L22-28)
```rust
/// Number of compute units that a block is allowed. A block's compute units are
/// accumulated by Transactions added to it; A transaction's compute units are
/// calculated by cost_model, based on transaction's signatures, write locks,
/// data size and built-in and SBF instructions.
pub const MAX_BLOCK_UNITS: u64 = MAX_BLOCK_UNITS_SIMD_0256;
pub const MAX_BLOCK_UNITS_SIMD_0256: u64 = 60_000_000;
pub const MAX_BLOCK_UNITS_SIMD_0286: u64 = 100_000_000;
```

**File:** core/src/banking_stage/consumer.rs (L530-562)
```rust
    fn try_add_processed_transaction_costs<'a, Tx: TransactionWithMeta>(
        bank: &Bank,
        transactions: &'a [Tx],
        mut transaction_costs: Vec<Option<TransactionCost<'a, Tx>>>,
        processing_results: &mut [TransactionProcessingResult],
        processed_counts: &mut ProcessedTransactionCounts,
        error_counters: &mut TransactionErrorMetrics,
        all_or_nothing: bool,
    ) -> (Vec<Option<TransactionCost<'a, Tx>>>, Vec<RetryableIndex>) {
        let mut retryable_transaction_indexes = Vec::with_capacity(processing_results.len());
        let mut all_or_nothing_error = None;
        let mut remaining_batch_error = None;
        let mut cost_tracker = bank.write_cost_tracker().unwrap();

        for (index, transaction_cost) in transaction_costs.iter_mut().enumerate() {
            let Some(cost) = transaction_cost.as_ref() else {
                continue;
            };

            match cost_tracker.try_add(cost) {
                Ok(_) => {}
                Err(err) => {
                    let transaction_error = TransactionError::from(err);
                    *transaction_cost = None;
                    if all_or_nothing {
                        all_or_nothing_error = Some((index, transaction_error));
                        break;
                    } else {
                        remaining_batch_error = Some((index, transaction_error));
                        break;
                    }
                }
            }
```

**File:** cost-model/src/cost_tracker.rs (L272-310)
```rust
    fn would_fit(
        &self,
        tx_cost: &TransactionCost<impl TransactionWithMeta>,
    ) -> Result<(), CostTrackerError> {
        let cost: u64 = tx_cost.sum();

        if self.block_cost().saturating_add(cost) > self.limits.block_cost {
            // check against the total package cost
            return Err(CostTrackerError::WouldExceedBlockMaxLimit);
        }

        // check if the transaction itself is more costly than the account_cost_limit
        if cost > self.limits.account_cost {
            return Err(CostTrackerError::WouldExceedAccountMaxLimit);
        }

        let allocated_accounts_data_size =
            self.allocated_accounts_data_size + Saturating(tx_cost.allocated_accounts_data_size());

        if allocated_accounts_data_size.0 > self.limits.allocated_data_size {
            return Err(CostTrackerError::WouldExceedAccountDataBlockLimit);
        }

        // check each account against account_cost_limit,
        for account_key in tx_cost.writable_accounts() {
            match self.cost_by_writable_accounts.get(account_key) {
                Some(chained_cost) => {
                    if chained_cost.saturating_add(cost) > self.limits.account_cost {
                        return Err(CostTrackerError::WouldExceedAccountMaxLimit);
                    } else {
                        continue;
                    }
                }
                None => continue,
            }
        }

        Ok(())
    }
```

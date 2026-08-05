## Title
Silent `block_cost` wraparound via unmatched `remove_transaction_cost` in `CostTracker` — (File: `cost-model/src/cost_tracker.rs`)

### Summary
The externally-cited bug is a bookkeeping split (`strategiesTotalStaked` / `_poolTotalStaked`) where amounts can be added to one bucket and later subtracted from the other, or subtracted without a matching prior addition, causing an unguarded integer underflow. Agave's `CostTracker` (`cost-model/src/cost_tracker.rs`) tracks an aggregate `block_cost` via `SharedBlockCost`, which is backed by a plain `AtomicU64` and uses `fetch_add`/`fetch_sub` for updates [1](#0-0) . Unlike the `Saturating<u64>` fields on `CostTracker` (`transaction_count`, `allocated_accounts_data_size`, etc.), `block_cost`'s underlying `fetch_sub` on `AtomicU64` does not saturate — it wraps modulo 2^64 on underflow, silently producing a near-`u64::MAX` value instead of panicking or reverting.

### Finding Description
`add_transaction_execution_cost` increments `block_cost` via `fetch_add`, and `sub_transaction_execution_cost` decrements it via `fetch_sub` [2](#0-1) . These are invoked from `try_add`/`remove` respectively [3](#0-2) .

In `Consumer` (banking stage), `try_add_processed_transaction_costs` adds each processed transaction's actual cost to `cost_tracker` and, on failure, calls `cost_tracker.remove(transaction_cost)` to roll back only the transactions that were already added for that failure path [4](#0-3) . Separately, if the PoH/record step fails after costs were added, `remove_added_transaction_costs` iterates the same `transaction_costs` slice and calls `cost_tracker.remove(transaction_cost)` for every `Some` entry [5](#0-4) , and this is also invoked on the `recording_result` error path in `execute_and_commit_transactions` [6](#0-5) .

This design mirrors the Solidity bug's structure exactly: costs are added to a shared counter through one code path and removed through another, with the correctness of the running total depending entirely on every "remove" being matched 1:1 with a prior "add", and never being called twice for the same cost or without a corresponding add. There is no invariant check (e.g., `checked_sub`, an assertion, or a saturating primitive) on `block_cost` guarding against a remove exceeding what was actually added — the atomic `fetch_sub` will silently wrap. If any code path caused `remove` to be called on a `TransactionCost` whose value was not fully reflected in the current `block_cost` (e.g., a batch being partially retried/re-added across the `all_or_nothing_error` vs `remaining_batch_error` branches in `try_add_processed_transaction_costs`, which both set the cost entry back to `None` after only partially rolling back the tracker in the `all_or_nothing` branch, at lines 565–581 vs 582–597), the resulting `block_cost` would not reflect a legitimate transaction total and would instead be some other value not bounded to `[0, u64::MAX]` in a meaningful sense.

### Impact Explanation
`would_fit` gates all future transaction admission into the current block against `self.block_cost().saturating_add(cost) > self.limits.block_cost` [7](#0-6) . If `block_cost` wraps to a value near `u64::MAX` due to an unmatched `remove`, `saturating_add` clamps to `u64::MAX`, which is always `> self.limits.block_cost`, so every subsequent transaction for that bank is rejected as `WouldExceedBlockMaxLimit`. This is a leader-local denial-of-service: the affected leader would produce empty or near-empty blocks for the remainder of that slot, degrading throughput/liveness for transactions routed to it, without requiring any privileged actor — an ordinary transaction submitter triggering the retry/rollback code paths in banking stage would be sufficient if such an unmatched-remove condition is reachable.

### Likelihood Explanation
This is speculative and **not conclusively demonstrated** from the code read so far. I was not able to fully trace, within the remaining tool budget, a concrete sequence in which `cost_tracker.remove()` is invoked without an exactly matching prior `add_transaction_cost()` for the same `TransactionCost` (i.e., a genuine double-remove or a remove for a larger cost value than was added). The code in `try_add_processed_transaction_costs` and `remove_added_transaction_costs` appears designed to keep `transaction_costs[i]` as the single source of truth (setting entries to `None` after they are "consumed" for removal), which is a reasonable guard against double counting. Whether this invariant holds across every call path (retries across scheduler threads, all-or-nothing batch semantics, and the recording-failure path in `execute_and_commit_transactions`) would require deeper tracing of `ProcessTransactionBatchOutput`/scheduler retry logic than was completed here.

### Recommendation
Given the uncertainty in confirming an actual double-remove/mismatched-remove path, this should be treated as a hardening recommendation rather than a confirmed exploit: replace `SharedBlockCost::fetch_sub`'s use of `AtomicU64::fetch_sub` with a saturating or checked subtraction (mirroring the `Saturating<u64>` fields already used elsewhere in `CostTracker`), and/or add a debug assertion or invariant check that `remove_transaction_cost` never decreases `block_cost` below the sum of currently-outstanding transaction costs, so a bookkeeping mismatch fails loudly (panic/log) instead of silently wrapping the tracker into a corrupted, DoS-inducing state.

### Proof of Concept
Not established. This report identifies the corrupted value (`SharedBlockCost` inside `CostTracker`) and the missing guard (non-saturating `fetch_sub` on `block_cost`), analogous to the Solidity `strategiesTotalStaked`/`_poolTotalStaked` underflow, but I could not construct or verify a concrete unprivileged transaction sequence that produces a mismatched add/remove within the scope of this investigation. If a Devin session with full repo/test access confirms such a path exists (e.g. via targeted testing of `Consumer::execute_and_commit_transactions` retry/rollback branches), the impact analysis above would apply directly.

### Citations

**File:** cost-model/src/cost_tracker.rs (L167-181)
```rust
    pub fn try_add(
        &mut self,
        tx_cost: &TransactionCost<impl TransactionWithMeta>,
    ) -> Result<UpdatedCosts, CostTrackerError> {
        self.would_fit(tx_cost)?;
        let updated_costliest_account_cost = self.add_transaction_cost(tx_cost);
        Ok(UpdatedCosts {
            updated_block_cost: self.block_cost(),
            updated_costliest_account_cost,
        })
    }

    pub fn remove(&mut self, tx_cost: &TransactionCost<impl TransactionWithMeta>) {
        self.remove_transaction_cost(tx_cost);
    }
```

**File:** cost-model/src/cost_tracker.rs (L272-286)
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
```

**File:** cost-model/src/cost_tracker.rs (L338-373)
```rust
    /// Apply additional actual execution units to cost_tracker
    /// Return the costliest account cost that were updated by `TransactionCost`
    fn add_transaction_execution_cost(
        &mut self,
        tx_cost: &TransactionCost<impl TransactionWithMeta>,
        adjustment: u64,
    ) -> u64 {
        let mut costliest_account_cost = 0;
        for account_key in tx_cost.writable_accounts() {
            let account_cost = self
                .cost_by_writable_accounts
                .entry(*account_key)
                .or_insert(0);
            *account_cost = account_cost.saturating_add(adjustment);
            costliest_account_cost = costliest_account_cost.max(*account_cost);
        }
        self.block_cost.fetch_add(adjustment);

        costliest_account_cost
    }

    /// Subtract extra execution units from cost_tracker
    fn sub_transaction_execution_cost(
        &mut self,
        tx_cost: &TransactionCost<impl TransactionWithMeta>,
        adjustment: u64,
    ) {
        for account_key in tx_cost.writable_accounts() {
            let account_cost = self
                .cost_by_writable_accounts
                .entry(*account_key)
                .or_insert(0);
            *account_cost = account_cost.saturating_sub(adjustment);
        }
        self.block_cost.fetch_sub(adjustment);
    }
```

**File:** cost-model/src/cost_tracker.rs (L393-414)
```rust
/// Wrapper around blockcost to allow fast sharing of the value without locking.
/// Value is read-only outside of cost-tracker.
#[derive(Debug, Clone)]
pub struct SharedBlockCost(Arc<AtomicU64>);

impl SharedBlockCost {
    pub fn new(value: u64) -> Self {
        Self(Arc::new(AtomicU64::new(value)))
    }

    fn fetch_add(&self, value: u64) -> u64 {
        self.0.fetch_add(value, Ordering::Release)
    }

    fn fetch_sub(&self, value: u64) -> u64 {
        self.0.fetch_sub(value, Ordering::Release)
    }

    pub fn load(&self) -> u64 {
        self.0.load(Ordering::Acquire)
    }
}
```

**File:** core/src/banking_stage/consumer.rs (L397-398)
```rust
        if let Err(recorder_err) = recording_result {
            Self::remove_added_transaction_costs(bank, &transaction_costs);
```

**File:** core/src/banking_stage/consumer.rs (L530-567)
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
        }

        if let Some((failed_index, transaction_error)) = all_or_nothing_error {
            for transaction_cost in transaction_costs[..failed_index].iter().flatten() {
                cost_tracker.remove(transaction_cost);
```

**File:** core/src/banking_stage/consumer.rs (L654-662)
```rust
    fn remove_added_transaction_costs<Tx: TransactionWithMeta>(
        bank: &Bank,
        transaction_costs: &[Option<TransactionCost<'_, Tx>>],
    ) {
        let mut cost_tracker = bank.write_cost_tracker().unwrap();
        for transaction_cost in transaction_costs.iter().flatten() {
            cost_tracker.remove(transaction_cost);
        }
    }
```

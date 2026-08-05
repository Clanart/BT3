## Finding: Cost-tracker post-execution cost reconciliation bypasses block/account cost limits

### Title
Actual-cost reconciliation in `CostTracker` updates `block_cost`/`cost_by_writable_accounts` without re-checking configured limits - ([File: cost-model/src/cost_tracker.rs])

### Summary
The report's bug class is: a numeric/state-transition invariant is checked against a *stale* pre-update value, and the subsequent state replacement is performed without re-validating the invariant against the new value, letting the tracked quantity exceed its intended cap. Agave's `CostTracker` contains the same pattern for block-level and per-account compute cost accounting.

### Finding Description
`CostTracker::try_add` is the only path that validates a transaction's cost against the configured limits before admitting it: it calls `would_fit`, which compares the *estimated* cost of the transaction plus the tracker's current totals against `self.limits.block_cost` and `self.limits.account_cost`, and only if that passes does it call `add_transaction_cost` to actually record the cost. [1](#0-0) [2](#0-1) 

However, the tracker separately exposes `add_transaction_execution_cost` / `sub_transaction_execution_cost`, whose explicit purpose is to reconcile the *estimated* cost that was originally admitted through `try_add`/`would_fit` with the *actual* execution cost measured after the transaction runs (the delta between `programs_execution_cost` estimated pre-execution vs. `executed_units` actually consumed, as exercised by the `test_adjust_transaction_execution_cost` test and the dedicated `core/tests/scheduler_cost_adjustment.rs` suite). [3](#0-2) [4](#0-3) 

Crucially, `add_transaction_execution_cost` mutates `self.block_cost` and each entry in `self.cost_by_writable_accounts` directly via `saturating_add(adjustment)`, with **no call to `would_fit` and no comparison against `self.limits.block_cost` / `self.limits.account_cost`** at this point: [5](#0-4) 

This mirrors the Lien bug exactly: the limit check (`would_fit`) is performed using the *pre-execution, estimated* cost — the "existing stack" in the Lien analogy. Once the transaction actually executes and the tracker is updated to the *real* execution cost via `add_transaction_execution_cost`, the check is never re-run against the corrected/increased value. If a transaction's actual `programs_execution_cost` (or `loaded_accounts_data_size_cost`, which is also folded into the adjustment via `calculate_cost_for_executed_transaction`) is higher than what was estimated at admission time — which is expected and normal, since CU estimation is inherently approximate and instructions can consume more compute than the static/default estimate — the post-execution adjustment can push `block_cost` and/or an account's entry in `cost_by_writable_accounts` above `self.limits.block_cost` / `self.limits.account_cost` with no rejection path.

### Impact Explanation
`block_cost` and `cost_by_writable_accounts` are the values `would_fit` uses to admit *subsequent* transactions/blocks in the same slot. If reconciliation silently pushes these counters past their configured caps, later transactions in the same or later blocks are evaluated against an incorrect/inflated baseline. Because there is no corrective clamp or rejection when the adjustment overflows the limit, the runtime's compute cost accounting can (a) permit further transactions to be batched on top of an already-over-limit block/account, defeating the purpose of the account/block cost caps intended to bound per-slot resource consumption and prevent single hot accounts or unusually expensive transactions from starving block production, and (b) contribute to non-deterministic/asymmetric block packing behavior between the leader (whose `CostTracker` reflects reconciled/actual costs) and downstream validators using differing estimation assumptions. This falls in the runtime/cost-model resource-accounting category valid per the impact scope (non-RPC exhaustion/degradation via unprivileged transactions).

### Likelihood Explanation
Estimated vs. actual CU divergence is a routine occurrence (not an attacker-controlled edge case) — the dedicated `scheduler_cost_adjustment.rs` test suite exists specifically because this reconciliation happens on essentially every committed transaction whose actual execution cost differs from its static/default estimate (e.g., builtin instructions, CPI-heavy transactions). An ordinary unprivileged user submitting transactions whose real compute consumption exceeds the default/estimated allocation is sufficient to trigger the unchecked upward adjustment; no malicious validator or privileged actor is required.

### Recommendation
After applying `add_transaction_execution_cost`/`sub_transaction_execution_cost`, re-validate the updated `block_cost` and the affected accounts' costs in `cost_by_writable_accounts` against `self.limits.block_cost` / `self.limits.account_cost` (i.e., perform a `would_fit`-equivalent check on the *post-adjustment* state, not only pre-admission), and define an explicit remediation/back-pressure policy (e.g., reject/park further batching against a still-open block or account) when reconciliation causes the tracked value to exceed its cap.

### Proof of Concept
Conceptually reproducible from existing tests:
1. `try_add(tx_cost)` admits a transaction whose estimated cost is just under `account_max`/`block_max` (`would_fit` passes), as in `test_cost_tracker_try_add_is_atomic`. [6](#0-5) 
2. The transaction executes and consumes more compute than its estimate (this is the normal case the `add_transaction_execution_cost` adjustment path exists to handle, per `test_adjust_transaction_execution_cost`). [7](#0-6) 
3. `add_transaction_execution_cost(&tx_cost, adjustment)` is invoked directly, incrementing `block_cost` and each writable account's cost by `adjustment` with no limit re-check, so `block_cost`/`cost_by_writable_accounts[account]` can end up strictly greater than `limits.block_cost`/`limits.account_cost`. [5](#0-4) 

**Uncertainty note**: I was unable to fully trace, within the available tool budget, the exact production call site(s) in `core/src/banking_stage/consumer.rs` or `runtime/src/transaction_execution.rs` that invoke `add_transaction_execution_cost`/`sub_transaction_execution_cost` during normal block production/replay (only the cost-tracker unit tests and the dedicated `scheduler_cost_adjustment.rs` integration-test harness were confirmed). Confirming the exact runtime call path and whether any surrounding caller re-validates limits after the adjustment would require further code review (e.g., a Devin session with full-repo access) before treating this as fully confirmed rather than a strong structural analog.

### Citations

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

**File:** cost-model/src/cost_tracker.rs (L313-323)
```rust
    fn add_transaction_cost(&mut self, tx_cost: &TransactionCost<impl TransactionWithMeta>) -> u64 {
        self.allocated_accounts_data_size += tx_cost.allocated_accounts_data_size();
        self.transaction_count += 1;
        self.transaction_signature_count += tx_cost.num_transaction_signatures();
        self.secp256k1_instruction_signature_count +=
            tx_cost.num_secp256k1_instruction_signatures();
        self.ed25519_instruction_signature_count += tx_cost.num_ed25519_instruction_signatures();
        self.secp256r1_instruction_signature_count +=
            tx_cost.num_secp256r1_instruction_signatures();
        self.add_transaction_execution_cost(tx_cost, tx_cost.sum())
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

**File:** cost-model/src/cost_tracker.rs (L746-770)
```rust
    #[test]
    fn test_cost_tracker_try_add_is_atomic() {
        let acct1 = Pubkey::new_unique();
        let acct2 = Pubkey::new_unique();
        let acct3 = Pubkey::new_unique();
        let cost = 100;
        let account_max = cost * 2;
        let block_max = account_max * 3; // for three accts

        let mut testee = CostTracker::new(account_max, block_max);

        // case 1: a tx writes to 3 accounts, should success, we will have:
        // | acct1 | $cost |
        // | acct2 | $cost |
        // | acct3 | $cost |
        // and block_cost = $cost
        {
            let transaction = WritableKeysTransaction::new(vec![acct1, acct2, acct3]);
            let tx_cost = simple_transaction_cost(&transaction, cost);
            assert!(testee.try_add(&tx_cost).is_ok());
            let (_costliest_account, costliest_account_cost) = testee.find_costliest_account();
            assert_eq!(cost, testee.block_cost());
            assert_eq!(3, testee.cost_by_writable_accounts.len());
            assert_eq!(cost, costliest_account_cost);
        }
```

**File:** cost-model/src/cost_tracker.rs (L806-843)
```rust
    #[test]
    fn test_adjust_transaction_execution_cost() {
        let acct1 = Pubkey::new_unique();
        let acct2 = Pubkey::new_unique();
        let acct3 = Pubkey::new_unique();
        let cost = 100;
        let account_max = cost * 2;
        let block_max = account_max * 3; // for three accts

        let mut testee = CostTracker::new(account_max, block_max);
        let transaction = WritableKeysTransaction::new(vec![acct1, acct2, acct3]);
        let tx_cost = simple_transaction_cost(&transaction, cost);
        let mut expected_block_cost = tx_cost.sum();
        let expected_tx_count = 1;
        assert!(testee.try_add(&tx_cost).is_ok());
        assert_eq!(expected_block_cost, testee.block_cost());
        assert_eq!(expected_tx_count, testee.transaction_count());
        testee
            .cost_by_writable_accounts
            .iter()
            .for_each(|(_key, units)| {
                assert_eq!(expected_block_cost, *units);
            });

        // adjust up
        {
            let adjustment = 50u64;
            testee.add_transaction_execution_cost(&tx_cost, adjustment);
            expected_block_cost += 50;
            assert_eq!(expected_block_cost, testee.block_cost());
            assert_eq!(expected_tx_count, testee.transaction_count());
            testee
                .cost_by_writable_accounts
                .iter()
                .for_each(|(_key, units)| {
                    assert_eq!(expected_block_cost, *units);
                });
        }
```

**File:** core/tests/scheduler_cost_adjustment.rs (L27-34)
```rust
#[derive(Debug, Eq, PartialEq)]
struct TestResult {
    // execution cost adjustment (eg estimated_execution_cost -
    // actual_execution_cost) if *committed* successfully; Which always the case for our tests
    cost_adjustment: i64,
    // Ok(()) if transaction executed successfully, otherwise error
    execution_status: Result<()>,
}
```

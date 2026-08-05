## Title
Cheap requested-CU-limit transactions can pin a hot account's cost budget, causing legitimate transactions writing that account to be rejected for the rest of the block - (File: `cost-model/src/cost_tracker.rs`, `cost-model/src/cost_model.rs`)

### Summary
The external report's core primitive is: a shared per-block resource cap that can be atomically/cheaply exhausted by an unprivileged actor at low cost, denying legitimate users access to that resource for the rest of the block/window. Agave has a direct structural analog in the `CostTracker`'s per-account write-lock cost budget (`account_cost` limit, default `MAX_WRITABLE_ACCOUNT_UNITS`), which is charged against the *requested* compute-unit limit rather than the transaction's actual execution cost until the transaction executes and is adjusted.

### Finding Description
`CostModel::get_estimated_execution_cost` sets the `programs_execution_cost` component of a transaction's cost to the *requested* `compute_unit_limit` from the transaction's `ComputeBudgetInstruction::SetComputeUnitLimit` (or the default/max cap if unspecified), not the amount of compute actually consumed: [1](#0-0) 

This estimated cost (`CostModel::calculate_cost`) is what gets checked/reserved against the `CostTracker`'s `would_fit`/`try_add` for the per-account (`account_cost`) and per-block (`block_cost`) limits: [2](#0-1) 

The per-account limit exists specifically "to prevent too many transactions write[ing] to same account, therefore reduce block's parallelism": [3](#0-2) 

The actual bank-level accounting reconciles the reservation to the *actual* executed units only after execution, via `add_transaction_execution_cost`/`sub_transaction_execution_cost` adjustments: [4](#0-3) 
and confirmed by the dedicated adjustment tests (`test_adjust_transaction_execution_cost`, and the `scheduler_cost_adjustment.rs` suite showing `cost_adjustment = requested_cu_limit - actual_executed_units`): [5](#0-4) [6](#0-5) 

The gap between "reserved on the requested CU limit" and "settled on actual usage" is the same class of issue as the external report: a resource cap that can be filled based on a cheap, attacker-controlled input (here, the requested CU limit or a maximal write-lock count) rather than the actual resource consumption, before it's corrected. An attacker can submit many transactions that write-lock a single popular/contended account (e.g., a widely used DEX/lending program's global state account) each requesting a large `compute_unit_limit` (up to `MAX_COMPUTE_UNIT_LIMIT`) while doing minimal actual work (or failing early/cheaply), driving `cost_by_writable_accounts[account]` for that account up to `MAX_WRITABLE_ACCOUNT_UNITS` quickly and cheaply. Any other transaction that also write-locks that same account will then be rejected with `WouldExceedMaxAccountCostLimit` for the remainder of that leader's block, denying legitimate users the ability to interact with that account — directly mirroring the deposit/redemption-cap DoS in the reported LevelMinting bug, but the resource here is a leader's per-slot per-account write budget.

### Impact Explanation
This is a non-RPC, remote, low-cost exhaustion of a specific shared limit that affects unprivileged, unrelated users trying to write to the same account within the same block — it does not require a malicious validator, leaked keys, or a trusted/privileged actor, only ordinary fee-paying transactions. The effect is degraded availability/censorship of a specific hot account for the remainder of a slot, at the cost of ordinary transaction fees.

### Likelihood Explanation
This requires no special privileges: any transaction can request a maximal CU limit and target the victim account as a write-lock. This is a well-known and intentional trade-off of the current cost-model design (the same limit exists precisely to bound single-account contention), and Solana already partially mitigates it through prioritization/fee markets and the requested-vs-actual cost adjustment after execution, which shrinks the reservation once the batch is processed. This limits — but does not eliminate — the attack window during a single leader's slot, and the effectiveness scales with how cheaply an attacker can produce many maximal-CU-limit, minimal-actual-cost transactions.

### Recommendation
Consider bounding the estimated (pre-execution) reservation used for the per-account cost check more tightly to typical/observed program costs rather than the full requested CU limit for a given writable account, or apply an anti-concentration fee/priority curve for the per-account cost budget (analogous to the report's own recommendation of imposing a fee on the resource-consuming action) so that inflating requested-but-unused CUs against a specific hot account is economically discouraged.

### Proof of Concept
A concrete PoC would require running an actual leader/banking-stage integration test, which is outside what can be demonstrated from static code inspection alone. Conceptually, based on the tests already in the codebase:
1. Deploy/target a widely-used program account `P`.
2. Submit many transactions writing to `P`, each with `ComputeBudgetInstruction::SetComputeUnitLimit(MAX_COMPUTE_UNIT_LIMIT)` and an instruction sequence that either fails immediately (cheap in `agave--015: cost-model/src/cost_model.rs:159-178` terms, the estimated cost is charged regardless of failure) or performs trivial actual work.
3. Once `cost_by_writable_accounts[P]` (per `cost-model/src/cost_tracker.rs:296-307`) approaches `MAX_WRITABLE_ACCOUNT_UNITS`, any further transaction writing to `P` from another user is rejected with `TransactionError::WouldExceedMaxAccountCostLimit` for the rest of the block, as validated by `test_cost_tracker_chain_reach_limit`/`test_cost_tracker_reach_limit` in `cost-model/src/cost_tracker.rs:597-644`.

**Note on confidence**: This analog is architecturally sound based on the code reviewed (estimated cost = requested CU limit until execution-time adjustment; per-account cap exists to bound contention), but I was not able to fully trace whether the initial "estimated cost" reservation is applied against the bank-level `CostTracker` synchronously *before* execution completes for every code path (vs. only being applied post-execution via `try_add_processed_transaction_costs`/`check_block_cost_limits`, which use *actual* executed-unit costs). If the pre-execution reservation only happens in the scheduler's local `budget`/`in_flight_tracker` (per-thread pacing) rather than in the shared bank `CostTracker`, the concentration effect on a single account may be smaller than described, since the bank-level per-account cap would primarily reflect actual, not requested, costs. This distinction should be verified by a Devin session with full access to `core/src/banking_stage/transaction_scheduler/` and `core/src/banking_stage/consumer.rs` execution flow before treating this as a confirmed, high-confidence vulnerability.

### Citations

**File:** cost-model/src/cost_model.rs (L159-178)
```rust
    fn get_estimated_execution_cost(
        transaction: &impl TransactionMeta,
        feature_set: &FeatureSet,
    ) -> (u64, u64) {
        // if failed to process compute_budget instructions, the transaction will not be executed
        // by `bank`, therefore it should be considered as no execution cost by cost model.
        let (programs_execution_costs, loaded_accounts_data_size_cost) =
            match transaction.transaction_configuration(feature_set) {
                Ok(config) => (
                    u64::from(config.compute_unit_limit),
                    Self::calculate_loaded_accounts_data_size_cost(
                        config.loaded_accounts_data_size_limit,
                        feature_set,
                    ),
                ),
                Err(_) => (0, 0),
            };

        (programs_execution_costs, loaded_accounts_data_size_cost)
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

**File:** cost-model/src/cost_tracker.rs (L806-859)
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

        // adjust down
        {
            let adjustment = 50u64;
            testee.sub_transaction_execution_cost(&tx_cost, adjustment);
            expected_block_cost -= 50;
            assert_eq!(expected_block_cost, testee.block_cost());
            assert_eq!(expected_tx_count, testee.transaction_count());
            testee
                .cost_by_writable_accounts
                .iter()
                .for_each(|(_key, units)| {
                    assert_eq!(expected_block_cost, *units);
                });
        }
    }
```

**File:** cost-model/src/block_cost_limits.rs (L30-33)
```rust
/// Number of compute units that a writable account in a block is allowed. The
/// limit is to prevent too many transactions write to same account, therefore
/// reduce block's parallelism.
pub const MAX_WRITABLE_ACCOUNT_UNITS: u64 = 24_000_000;
```

**File:** core/tests/scheduler_cost_adjustment.rs (L239-262)
```rust
#[test]
fn test_builtin_ix_cost_adjustment_with_cu_limit_high() {
    let mut test_setup = TestSetup::new();
    let cu_limit: u32 = 500_000;

    // A simple transfer ix, and request cu-limit to more than needed
    // Cost model & Compute budget: reserve/allocate requested CU Limit `500_000`
    // VM Execution: consume CUs for `system` and `compute-budget` programs, then success
    // Result: adjustment = 500_000 - 150 -150
    let expected = TestResult {
        cost_adjustment: cu_limit as i64
            - solana_system_program::system_processor::DEFAULT_COMPUTE_UNITS as i64
            - solana_compute_budget_program::DEFAULT_COMPUTE_UNITS as i64,
        execution_status: Ok(()),
    };

    assert_eq!(
        expected,
        test_setup.execute_test_transaction(&[
            test_setup.transfer_ix(),
            test_setup.set_cu_limit_ix(cu_limit),
        ],)
    );
}
```

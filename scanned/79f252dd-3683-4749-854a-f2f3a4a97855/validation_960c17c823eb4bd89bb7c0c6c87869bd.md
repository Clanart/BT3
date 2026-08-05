## Analysis

The Tessera bug reduces to one invariant: **a permissionless "supersede" action is gated on a single scalar comparison (price) while the resource actually meant to make replacement costly (collateral) is left unconstrained**, letting an attacker occupy the "winning" slot at negligible cost and block legitimate, higher-value use of the mechanism until the attacker chooses to release it.

The closest verified analog in Agave's local code is the **compute-unit cost tracker's per-account admission control**, which gates write access to an account purely on aggregate compute-unit cost, with no notion of the fee/priority value being paid. [1](#0-0) 

### Title
Fee-blind per-account cost limit lets low-value transactions occupy an account's entire block budget, blocking higher-value transactions - (File: `cost-model/src/cost_tracker.rs`)

### Summary
`CostTracker::try_add` admits or rejects a transaction against an account purely by comparing accumulated compute-unit cost (`cost_by_writable_accounts`) to `MAX_WRITABLE_ACCOUNT_UNITS` (24,000,000 CU). It never compares the *value* (priority fee) of the incoming transaction to the value of the transactions that already reserved that budget. This is the same one-dimensional guard pattern as the OptimisticListing bug: the check enforces a resource ceiling (CU / "collateral" analog) but ignores the value dimension (fee / "price" analog) that was supposed to make squatting economically costly. [2](#0-1) [3](#0-2) 

### Finding Description
`try_add` walks each writable account key and, if `cost_by_writable_accounts[account] + cost > account_cost_limit`, rejects the transaction with `WouldExceedAccountMaxLimit`; otherwise it commits the low-cost transaction's reservation immediately and irrevocably for the block: [4](#0-3) 

There is no rollback, eviction, or replacement path once a transaction's cost has been applied to `cost_by_writable_accounts` — a later, much higher-fee transaction touching the same account simply fails with `WouldExceedAccountMaxLimit` and cannot bump out the cheaper reservations, as shown by the "hot account" test that demonstrates a filled account rejecting any further writer regardless of that writer's fee: [5](#0-4) 

This mirrors the Tessera flaw precisely: the only enforced invariant is a scalar resource ceiling (CU budget, analogous to "collateral must exist"), while the value dimension that should make squatting costly (priority fee, analogous to "price") is completely unconstrained. An attacker only needs enough minimal-CU, minimal-fee transactions writing to a single popular/hot account (e.g. a shared program state account, an AMM pool, a popular NFT/marketplace account) to drive `cost_by_writable_accounts[account]` to `MAX_WRITABLE_ACCOUNT_UNITS`. Because `WRITE_LOCK_UNITS` (300 CU per write lock) plus a minimal signature cost is enough baseline cost per transaction, this requires only ordinary base fees (paid once per 400ms slot, repeatable every block), not any special privilege, stake, or leader/validator role. [6](#0-5) 

Once the account's budget is filled for the block, *every* other transaction touching that account — no matter how much priority fee it offers — is rejected for the remainder of that block, exactly like the "collateral=1, price=existing-1" listing that blocks any legitimately-priced proposal until the attacker chooses to withdraw it. And, just as in the Tessera report, the attacker can repeat this every single block (re-filing minimal-cost transactions each slot) at negligible cost, holding the account "hostage" indefinitely and preventing it from ever being "reasonably" written to by a paying/legitimate transaction, unless the attacker stops.

### Impact Explanation
This is a non-RPC, remote, unprivileged denial-of-service against a specific account's usability within Agave's runtime/cost-model layer: a normal fee-payer, with no stake and no special role, can indefinitely deny legitimate transactions (including transactions offering arbitrarily higher priority fees) from writing to a targeted account for every block, by filling `MAX_WRITABLE_ACCOUNT_UNITS` with minimal-cost transactions before the legitimate transaction lands. This can be leveraged to block time-sensitive protocol operations (liquidations, oracle updates, auctions) on any account that becomes a target, matching the "prevent a user from ... never letting [something] be reasonably bought/executed" impact class from the seed report.

### Likelihood Explanation
High. No stake, leader status, or malicious-validator assumption is required — only the ability to send ordinary fee-paying transactions, which is available to any client. The `MAX_WRITABLE_ACCOUNT_UNITS` ceiling (24M CU) divided by the minimal per-transaction cost (write-lock + signature cost, tens of CU) yields a very large number of cheap transactions needed per block, but this is a purely mechanical/economic cost, not a security barrier, and is renewable every slot.

### Recommendation
Introduce fee/priority-awareness into the per-account admission path: e.g., allow a higher-priority-fee transaction to evict/replace already-reserved low-fee cost within the same block (similar to a priority queue with preemption), or size `MAX_WRITABLE_ACCOUNT_UNITS` reservations proportionally to fee paid, or reserve a fraction of each account's per-block budget for a priority-fee-sorted admission pass rather than first-come-first-served regardless of value.

### Proof of Concept
Using the existing test harness pattern in `cost-model/src/cost_tracker.rs`:
1. Construct N transactions all writing to `hot_account`, each with the same minimal `cost` (as in `test_try_add_rollback_many_accounts`), and call `testee.try_add(&tx_cost)` repeatedly until `cost_by_writable_accounts[hot_account] == account_cost` limit is reached.
2. Construct one final transaction writing to `hot_account` with a very high (simulated) priority fee but the same or similar CU cost.
3. Call `try_add` for the high-fee transaction — observe it is rejected with `CostTrackerError::WouldExceedAccountMaxLimit`, exactly as the low-fee reservations from step 1, demonstrating that fee has no bearing on admission once the account budget is exhausted. [7](#0-6)

### Citations

**File:** cost-model/src/cost_tracker.rs (L172-222)
```rust
    pub fn try_add(
        &mut self,
        tx_cost: &TransactionCost<impl TransactionWithMeta>,
    ) -> Result<UpdatedCosts, CostTrackerError> {
        let cost = tx_cost.sum();

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

        // Check each account against account_cost_limit and apply the cost in
        // the same lookup. On failure, undo the applied prefix.
        let mut updated_costliest_account_cost = 0;
        for (index, account_key) in tx_cost.writable_accounts().enumerate() {
            let new_account_cost = match self.cost_by_writable_accounts.entry(*account_key) {
                Entry::Occupied(mut entry) => {
                    let new_account_cost = entry.get().saturating_add(cost);
                    if new_account_cost > self.limits.account_cost {
                        None
                    } else {
                        *entry.get_mut() = new_account_cost;
                        Some(new_account_cost)
                    }
                }
                Entry::Vacant(entry) => {
                    // `cost <= limits.account_cost` was checked above, so an
                    // account without chained cost always fits
                    entry.insert(cost);
                    Some(cost)
                }
            };
            let Some(new_account_cost) = new_account_cost else {
                // the first `index` accounts were applied before this failure
                self.roll_back_applied_costs(tx_cost, cost, index);
                return Err(CostTrackerError::WouldExceedAccountMaxLimit);
            };
            updated_costliest_account_cost = updated_costliest_account_cost.max(new_account_cost);
        }
```

**File:** cost-model/src/cost_tracker.rs (L599-620)
```rust
    #[test]
    fn test_cost_tracker_chain_reach_limit() {
        let mint_keypair = test_setup();
        // build two transactions with same signed account
        let tx1 = build_simple_transaction(&mint_keypair);
        let tx_cost1 = simple_transaction_cost(&tx1, 5);
        let cost1 = tx_cost1.sum();
        let tx2 = build_simple_transaction(&mint_keypair);
        let tx_cost2 = simple_transaction_cost(&tx2, 5);
        let cost2 = tx_cost2.sum();

        // build testee to have capacity for two simple transactions, but not for same accounts
        let mut testee = CostTracker::new(cmp::min(cost1, cost2), cost1 + cost2);
        // should have room for first transaction
        {
            assert!(testee.try_add(&tx_cost1).is_ok());
        }
        // but no more sapce on the same chain (same signer account)
        {
            assert!(testee.try_add(&tx_cost2).is_err());
        }
    }
```

**File:** cost-model/src/cost_tracker.rs (L826-856)
```rust
    #[test]
    fn test_try_add_rollback_many_accounts() {
        let cost = 100;
        let hot_account = Pubkey::new_unique();
        let mut testee = CostTracker::new(cost * 2, cost * 1000);

        // drive hot_account to the limit so the next charge fails
        let transaction = WritableKeysTransaction::new(vec![hot_account]);
        let tx_cost = simple_transaction_cost(&transaction, cost);
        assert!(testee.try_add(&tx_cost).is_ok());
        assert!(testee.try_add(&tx_cost).is_ok());
        let block_cost_before = testee.block_cost();

        // 100 fresh accounts followed by hot_account, all 100 fresh entries
        // are inserted before the failure at index 100
        let mut keys: Vec<Pubkey> = (0..100).map(|_| Pubkey::new_unique()).collect();
        keys.push(hot_account);
        let transaction = WritableKeysTransaction::new(keys);
        let tx_cost = simple_transaction_cost(&transaction, cost);
        assert!(matches!(
            testee.try_add(&tx_cost),
            Err(CostTrackerError::WouldExceedAccountMaxLimit)
        ));

        assert_eq!(1, testee.cost_by_writable_accounts.len());
        assert_eq!(
            Some(&(cost * 2)),
            testee.cost_by_writable_accounts.get(&hot_account)
        );
        assert_eq!(block_cost_before, testee.block_cost());
    }
```

**File:** cost-model/src/block_cost_limits.rs (L17-20)
```rust
/// Number of compute units for one write lock
pub const WRITE_LOCK_UNITS: u64 = COMPUTE_UNIT_TO_US_RATIO * 10;
/// Number of data bytes per compute units
pub const INSTRUCTION_DATA_BYTES_COST: u64 = 140 /*bytes per us*/ / COMPUTE_UNIT_TO_US_RATIO;
```

**File:** cost-model/src/block_cost_limits.rs (L30-33)
```rust
/// Number of compute units that a writable account in a block is allowed. The
/// limit is to prevent too many transactions write to same account, therefore
/// reduce block's parallelism.
pub const MAX_WRITABLE_ACCOUNT_UNITS: u64 = 24_000_000;
```

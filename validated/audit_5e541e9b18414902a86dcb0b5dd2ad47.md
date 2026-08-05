## Title
Integer-division truncation in `get_instructions_data_cost` lets attackers under-charge instruction-data cost against block/account CU limits - ([File: cost-model/src/cost_model.rs])

### Summary
`CostModel::get_instructions_data_cost` computes the data-bytes portion of a transaction's cost with a plain truncating integer division: [1](#0-0) 

```rust
fn get_instructions_data_cost(transaction: &impl TransactionMeta) -> u16 {
    transaction.instruction_data_len() / (INSTRUCTION_DATA_BYTES_COST as u16)
}
```

This is structurally identical to the Hedge Vault bug: a numerator that is not a clean multiple of the divisor is silently rounded down, and the truncated remainder (up to `INSTRUCTION_DATA_BYTES_COST - 1` bytes of data, per instruction data payload) is never charged to anything. The result feeds directly into `TransactionCost::sum()` [2](#0-1)  which is the exact value the `CostTracker::try_add` enforcement path uses to accept or reject a transaction against the block/account CU limits [3](#0-2) .

### Finding Description
`get_instructions_data_cost` truncates `instruction_data_len() / INSTRUCTION_DATA_BYTES_COST` toward zero. Unlike the `calculate_pages_for_bytes` helper in the same file — which explicitly rounds *up* by adding `page_size - 1` before dividing, specifically to avoid under-charging for the loaded-accounts-data-size cost [4](#0-3)  — the instruction-data-bytes cost has no such compensation. This is the same pattern the external report calls out for the Hedge Vault `denormalize` function: a division whose fractional remainder is dropped instead of rounded up, letting the "amount charged" for consuming a resource under-represent the resource actually consumed.

In Agave, `data_bytes_cost` is one component that `CostTracker::try_add` sums into `cost` and checks against `self.limits.block_cost` and `self.limits.account_cost` before permitting a transaction to be scheduled/executed [3](#0-2) . Because the division always rounds down, a transaction's true resource footprint (bytes of instruction data actually serialized, parsed, and copied around by the scheduler/SVM) is understated by up to `INSTRUCTION_DATA_BYTES_COST - 1` bytes worth of cost units per transaction. No existing guard restores the discarded remainder: `TransactionCost::sum()` simply adds the already-truncated `u16` value [2](#0-1) , and `CostTracker` has no compensating floor/ceiling logic for this specific field.

### Impact Explanation
This does not create fake lamports or tokens (there's no equivalent "loan" ledger in Agave's cost model), but it is the direct analog of the underlying bug class: the accounting number that gates a scarce resource (block cost units, and transitively CPU/serialization time in the leader) is computed with a floor instead of a ceiling. An unprivileged transaction sender can craft many transactions whose instruction-data length sits just under multiples of `INSTRUCTION_DATA_BYTES_COST`, causing the tracked "data bytes cost" for those transactions to be less than their real proportional share, while still consuming full CPU/parsing/serialization work in the leader. Repeated across many transactions in a block, this systematically under-counts real load against the enforced block cost limit, which is the kind of "cause ... non-RPC remote exhaustion/crash" outcome in scope. That said, `data_bytes_cost` is bounded (max instruction data is capped by packet size, `u16`), so the per-transaction magnitude of the discrepancy is small; the impact is a cumulative/statistical resource-accounting bias rather than a large single-shot theft, and I could not fully verify (without deeper study of `INSTRUCTION_DATA_BYTES_COST`'s numeric value and its weight relative to `programs_execution_cost`/`loaded_accounts_data_size_cost` in the overall `sum()`) how material this bias is in practice relative to other cost components that dominate `TransactionCost::sum()`.

### Likelihood Explanation
Likelihood is low-to-moderate. The bug is trivially triggerable by any unprivileged transaction sender (just pick instruction-data lengths that don't divide evenly by `INSTRUCTION_DATA_BYTES_COST`), requires no special privileges, and needs no malicious validator/leader collusion. However, because `data_bytes_cost` is only one of several additive components in `TransactionCost::sum()` (alongside `signature_cost`, `write_lock_cost`, `programs_execution_cost`, `loaded_accounts_data_size_cost`), and the maximum per-transaction under-count is capped by `INSTRUCTION_DATA_BYTES_COST - 1`, the practical exhaustion effect is likely marginal compared to more dominant cost terms — this needs empirical measurement to confirm real-world significance.

### Recommendation
Round the instruction-data-bytes cost up (ceiling division), mirroring the pattern already used for `calculate_pages_for_bytes`:

```rust
fn get_instructions_data_cost(transaction: &impl TransactionMeta) -> u16 {
    let len = transaction.instruction_data_len();
    let divisor = INSTRUCTION_DATA_BYTES_COST as u16;
    len.saturating_add(divisor - 1) / divisor
}
```

### Proof of Concept
A concrete PoC requires knowing the exact value of `INSTRUCTION_DATA_BYTES_COST` (defined in `cost-model/src/block_cost_limits.rs`, which I located but did not read in full) to construct transactions whose instruction-data length is one byte short of a multiple of that constant, then compare `CostModel::calculate_cost(...).data_bytes_cost()` for `len` vs `len - 1` to show the cost stays flat at the boundary (as already partially demonstrated by the existing unit tests `test_zero_bytes`/`test_non_zero_bytes_single_page` for the analogous `calculate_pages_for_bytes`, which do use ceiling rounding, contrasted against `get_instructions_data_cost`, which does not) [5](#0-4) [1](#0-0) . I was not able to fully verify the numeric significance of this gap in the time available; a Devin session with full repo access could pull `INSTRUCTION_DATA_BYTES_COST`'s value and run the cost-model test suite to quantify the effect precisely.

### Citations

**File:** cost-model/src/cost_model.rs (L180-183)
```rust
    /// Return the instruction data bytes cost.
    fn get_instructions_data_cost(transaction: &impl TransactionMeta) -> u16 {
        transaction.instruction_data_len() / (INSTRUCTION_DATA_BYTES_COST as u16)
    }
```

**File:** cost-model/src/cost_model.rs (L185-190)
```rust
    /// Compute the number of pages needed to contain provided number of bytes.
    fn calculate_pages_for_bytes(bytes: u32) -> u64 {
        u64::from(bytes)
            .saturating_add(ACCOUNT_DATA_COST_PAGE_SIZE.saturating_sub(1))
            .saturating_div(ACCOUNT_DATA_COST_PAGE_SIZE)
    }
```

**File:** cost-model/src/cost_model.rs (L911-933)
```rust
    fn test_zero_bytes() {
        // 0 bytes should result in 0 pages and 0 cost
        assert_eq!(CostModel::calculate_pages_for_bytes(0), 0);
        assert_eq!(CostModel::calculate_pages_cost(0), 0);
        assert_eq!(
            CostModel::calculate_loaded_accounts_data_size_cost(0, &FeatureSet::default()),
            0
        );
    }

    #[test]
    fn test_non_zero_bytes_single_page() {
        let page_size = ACCOUNT_DATA_COST_PAGE_SIZE as u32;

        // Any non-zero bytes up to page_size should be 1 page
        assert_eq!(CostModel::calculate_pages_for_bytes(1), 1);
        assert_eq!(CostModel::calculate_pages_for_bytes(page_size), 1);

        assert_eq!(
            CostModel::calculate_loaded_accounts_data_size_cost(1, &FeatureSet::default()),
            CostModel::calculate_pages_cost(1)
        );
    }
```

**File:** cost-model/src/transaction_cost.rs (L18-25)
```rust
impl<'a, Tx> TransactionCost<'a, Tx> {
    pub fn sum(&self) -> u64 {
        self.signature_cost
            .saturating_add(self.write_lock_cost)
            .saturating_add(u64::from(self.data_bytes_cost))
            .saturating_add(self.programs_execution_cost)
            .saturating_add(self.loaded_accounts_data_size_cost)
    }
```

**File:** cost-model/src/cost_tracker.rs (L172-186)
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
```

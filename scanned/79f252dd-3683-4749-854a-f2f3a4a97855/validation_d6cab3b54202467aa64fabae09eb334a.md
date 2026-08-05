## Title
Floor-division truncation in `CostModel::get_instructions_data_cost` lets attackers evade instruction-data cost accounting - ([File: cost-model/src/cost_model.rs])

### Summary
The external report describes `SimpleBondingCurve::_getQuoteAmount` using unguarded integer division (`(quoteReserve * baseAmount) / baseReserveAfter`), which truncates towards zero and lets a user systematically underpay by choosing inputs that land on the floor side of the division. The Agave analog is `CostModel::get_instructions_data_cost` in `cost-model/src/cost_model.rs:181-183`, which computes the "data bytes" component of a transaction's accounted cost with a plain floor division on a fully attacker-controlled input (`instruction_data_len()`), with no rounding-up/ceiling logic, unlike the sibling `calculate_pages_for_bytes` function in the same file which explicitly rounds up. [1](#0-0) 

### Finding Description
`get_instructions_data_cost` is:
```rust
fn get_instructions_data_cost(transaction: &impl TransactionMeta) -> u16 {
    transaction.instruction_data_len() / (INSTRUCTION_DATA_BYTES_COST as u16)
}
``` [1](#0-0) 

This is invoked from `calculate_cost`, `calculate_cost_for_executed_transaction`, and `estimate_cost` — all core paths that produce the `TransactionCost` used both pre-execution (admission to the block via `CostTracker`/block cost limits) and post-execution accounting. [2](#0-1) [3](#0-2) [4](#0-3) 

The corrupted value is `data_bytes_cost` (a `u16` field of `TransactionCost`), which directly feeds `TransactionCost::sum()` and is compared against `MAX_BLOCK_UNITS`/per-account and per-block cost limits in `cost_tracker.rs`. `instruction_data_len()` is entirely attacker-controlled (the total serialized byte length of all instruction data in the transaction). Because the division is a plain floor `/` with no `+ (COST-1)` ceiling adjustment (contrast with `calculate_pages_for_bytes`, which explicitly does `bytes.saturating_add(PAGE_SIZE - 1).saturating_div(PAGE_SIZE)` to round up), any instruction data whose length is not an exact multiple of `INSTRUCTION_DATA_BYTES_COST` has its remainder bytes (up to `INSTRUCTION_DATA_BYTES_COST - 1` bytes) accounted as **zero cost**. [5](#0-4) 

An attacker crafting many transactions each with `instruction_data_len` = `k * INSTRUCTION_DATA_BYTES_COST - 1` (i.e., one byte short of the next cost unit) repeatedly gets a "free" remainder of nearly a full cost unit's worth of data per transaction. Aggregated over many transactions in a block/slot, this compounds exactly like the reported bug: the truncation is systematically exploitable and the loss (unaccounted cost) accumulates with volume, since every transaction independently benefits from the same floor-rounding gap.

### Impact Explanation
`data_bytes_cost` is summed into the total `TransactionCost` that `CostTracker` uses to enforce block-level and per-writable-account cost limits (`cost_tracker.rs`) and that the cost model's leader/scheduler logic uses for block packing. Because the floor division systematically under-counts the cost of instruction-data-bearing transactions, an attacker can pack more actual instruction-data bytes into a block than the cost-accounting model believes it is admitting, letting them push real resource consumption (serialization/deserialization, bandwidth in the block) beyond what the cost limits were designed to bound. This does not directly move lamports like the bonding-curve case, but it is the direct structural analog: unprivileged, transaction-controlled floor division that lets the attacker pay less (in accounted cost) than the true resource consumption, which is exactly the "systematic under-pricing that scales with attacker-chosen inputs" pattern from the report. This falls into the non-RPC remote resource-exhaustion category (cost-model under-accounting enabling excess resource consumption within nominal cost limits), not fund theft.

### Likelihood Explanation
High feasibility for triggering the truncation: `instruction_data_len()` is fully determined by the transaction's own instruction data, which any unprivileged transaction sender controls precisely (e.g., padding instruction data to `k*INSTRUCTION_DATA_BYTES_COST - 1` bytes). No special privileges, trusted plugins, or malicious-peer assumptions are required — it's a standard transaction submitted through the normal transaction ingestion path. The per-transaction magnitude of the gap is bounded by `INSTRUCTION_DATA_BYTES_COST - 1` cost units, but because it recurs on every transaction, an attacker submitting many such transactions accumulates a meaningful discrepancy between accounted and real cost, analogous to "repeated targeted small buys" draining value in the original report.

### Recommendation
Change `get_instructions_data_cost` to use ceiling division, consistent with `calculate_pages_for_bytes` in the same file:
```rust
fn get_instructions_data_cost(transaction: &impl TransactionMeta) -> u16 {
    let len = transaction.instruction_data_len();
    len.saturating_add(INSTRUCTION_DATA_BYTES_COST as u16 - 1) / (INSTRUCTION_DATA_BYTES_COST as u16)
}
```
Add a regression test mirroring `test_non_zero_bytes_single_page`/`test_non_zero_bytes_multiple_pages` (which already validate rounding-up behavior for `calculate_pages_for_bytes`) for `get_instructions_data_cost`, verifying that any non-multiple-of-`INSTRUCTION_DATA_BYTES_COST` length rounds up rather than truncating.

### Proof of Concept
1. Craft a transaction whose total instruction data length is `n = k * INSTRUCTION_DATA_BYTES_COST - 1` for some `k` (e.g. one byte short of a cost-unit boundary).
2. Call `CostModel::calculate_cost` (or submit the transaction normally so it goes through `get_estimated_execution_cost`/`calculate_transaction_cost`).
3. Observe `tx_cost.data_bytes_cost` equals `k - 1` instead of the "true" resource usage that would round to `k` cost units under a ceiling scheme — i.e., `(n) / INSTRUCTION_DATA_BYTES_COST == k - 1`, discarding `INSTRUCTION_DATA_BYTES_COST - 1` bytes worth of cost for free.
4. Repeat across many transactions in the same block/slot; the aggregate uncounted data-cost grows linearly with the number of such crafted transactions, while `calculate_pages_for_bytes`-based accounting (loaded-accounts-data-size cost) correctly rounds up for the same class of input, confirming the inconsistency is specific to `get_instructions_data_cost`.

Note: I was not able to fully trace how `data_bytes_cost`'s `u16` truncation-of-cost interacts with the exact numeric value of `INSTRUCTION_DATA_BYTES_COST` (defined in `cost-model/src/block_cost_limits.rs`) or quantify the precise per-block worst-case aggregate loss from local code alone; a background Devin session with full repo access could pull the exact constant and enforcement thresholds in `cost_tracker.rs` to bound the worst-case exploitable gap more precisely.

### Citations

**File:** cost-model/src/cost_model.rs (L36-52)
```rust
    pub fn calculate_cost<'a, Tx: TransactionMeta + SVMStaticMessage>(
        transaction: &'a Tx,
        feature_set: &FeatureSet,
    ) -> TransactionCost<'a, Tx> {
        let (programs_execution_cost, loaded_accounts_data_size_cost) =
            Self::get_estimated_execution_cost(transaction, feature_set);
        let data_bytes_cost = Self::get_instructions_data_cost(transaction);
        Self::calculate_transaction_cost(
            transaction,
            transaction.program_instructions_iter(),
            transaction.num_write_locks(),
            programs_execution_cost,
            loaded_accounts_data_size_cost,
            data_bytes_cost,
            feature_set,
        )
    }
```

**File:** cost-model/src/cost_model.rs (L56-77)
```rust
    pub fn calculate_cost_for_executed_transaction<'a, Tx: TransactionMeta + SVMStaticMessage>(
        transaction: &'a Tx,
        actual_programs_execution_cost: u64,
        actual_loaded_accounts_data_size_bytes: u32,
        feature_set: &FeatureSet,
    ) -> TransactionCost<'a, Tx> {
        let loaded_accounts_data_size_cost = Self::calculate_loaded_accounts_data_size_cost(
            actual_loaded_accounts_data_size_bytes,
            feature_set,
        );
        let instructions_data_cost = Self::get_instructions_data_cost(transaction);

        Self::calculate_transaction_cost(
            transaction,
            transaction.program_instructions_iter(),
            transaction.num_write_locks(),
            actual_programs_execution_cost,
            loaded_accounts_data_size_cost,
            instructions_data_cost,
            feature_set,
        )
    }
```

**File:** cost-model/src/cost_model.rs (L83-101)
```rust
    pub fn estimate_cost<'a, Tx: TransactionMeta>(
        transaction: &'a Tx,
        instructions: impl Iterator<Item = (&'a Pubkey, SVMInstruction<'a>)>,
        num_write_locks: u64,
        feature_set: &FeatureSet,
    ) -> TransactionCost<'a, Tx> {
        let (programs_execution_cost, loaded_accounts_data_size_cost) =
            Self::get_estimated_execution_cost(transaction, feature_set);
        let data_bytes_cost = Self::get_instructions_data_cost(transaction);
        Self::calculate_transaction_cost(
            transaction,
            instructions,
            num_write_locks,
            programs_execution_cost,
            loaded_accounts_data_size_cost,
            data_bytes_cost,
            feature_set,
        )
    }
```

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

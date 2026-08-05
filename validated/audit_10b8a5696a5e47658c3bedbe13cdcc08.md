### Title
Zero-priority-fee transactions receive full default compute-unit grants, letting unprivileged users grief leader compute at negligible cost - ([File: compute-budget-instruction/src/compute_budget_instruction_details.rs])

### Summary
Hinkal's issue is that a fee (percentage-of-amount) is decoupled from the actual gas/compute a user-controlled hook can burn, so the party executing the transaction (the relayer) can be forced to spend far more resources than it is paid for. The Agave analog is structurally the same decoupling, but the "relayer" is the leader/validator that must execute every admitted transaction: the network charges a flat per-signature fee regardless of the compute-unit budget actually granted to run arbitrary (possibly attacker-deployed) BPF programs, and the priority/compute-unit-price mechanism that is supposed to align payment with compute cost is purely optional and defaults to zero.

### Finding Description
When a transaction does not include a `ComputeBudgetInstruction::SetComputeUnitLimit`, Agave auto-computes a default CU grant per instruction: [1](#0-0) 
Each non-builtin (BPF) instruction is granted `DEFAULT_INSTRUCTION_COMPUTE_UNIT_LIMIT = 200_000` CU by default, and each builtin gets `MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT = 3_000` CU, capped only by the global `MAX_COMPUTE_UNIT_LIMIT = 1_400_000`: [2](#0-1) 

Independently, `compute_unit_price` (the only knob that ties fee to compute) defaults to `0` when the user does not request it: [3](#0-2) 

The cost model used for both block-inclusion accounting and network-wide scheduling directly uses the requested/derived `compute_unit_limit` as the "programs_execution_cost," decoupled from what the transaction actually pays: [4](#0-3) 

Priority for forwarding/leader admission is computed as `reward / (1 + cost)`, where `reward` is driven almost entirely by `compute_unit_price` (which can legitimately be `0`) while `cost` is the cost-model-estimated compute (which can legitimately be up to `1_400_000`): [5](#0-4) 

This means a user can submit a transaction whose instructions CPI into one or more caller-controlled/attacker-deployed BPF programs ("hooks," in the Hinkal analogy) that deliberately consume large amounts of compute (bounded only by `MAX_COMPUTE_UNIT_LIMIT` and the per-CPI accounting in `program-runtime/src/cpi.rs`), while paying `compute_unit_price = 0` and only the flat per-signature fee: [6](#0-5) 
The block-level guard only limits the *aggregate* compute consumed per slot (`MAX_BLOCK_UNITS = 60_000_000`) and per writable account (`MAX_WRITABLE_ACCOUNT_UNITS = 24_000_000`): [7](#0-6) 
Neither guard enforces a minimum compute-unit-price relative to the requested/derived compute-unit-limit, so nothing stops many such near-zero-priority, high-compute transactions from being packed by a leader up to the block ceiling — the leader (the "relayer" in this analog) does the CPU/wall-clock work of executing up to 1.4M CU per transaction while collecting only the fixed signature fee (`SIGNATURE_COST` units, ~5000 lamports economically) in return.

### Impact Explanation
This falls into the "non-RPC remote exhaustion" category for unprivileged transaction/CPI paths: an attacker can flood the TPU/QUIC ingestion path with many transactions that (a) look cheap by the fee/priority heuristic used for admission and scheduling, but (b) are each entitled to consume up to the maximum default compute grant via CPI into attacker-controlled programs, without ever paying a compute-unit price. Because scheduling/forwarding priority is `reward/cost` and `reward` can be built almost entirely from the flat signature fee (with `compute_unit_price = 0`), this class of transaction is treated as "cheap" by the very metric intended to price compute, even though it consumes maximal compute. This can degrade leader throughput and crowd out honest, fee-paying users' transactions relative to the compute actually purchased — mirroring the Hinkal finding where the executor (relayer/leader) bears unrecovered resource cost.

### Likelihood Explanation
Likelihood is bounded because: (1) Solana's priority-fee market and stake-weighted QoS/QUIC connection admission are explicit mitigations already in place and were designed to deprioritize exactly this kind of low-fee/high-compute transaction under congestion; (2) this is fundamentally the same trade-off Solana's fee design has always made (flat base fee + optional priority fee), which is analogous to Hinkal's "Acknowledged"/"desired behavior" response rather than an unpatched bug; and (3) exploiting it for meaningful degradation requires sustained transaction volume (bandwidth/signature cost) rather than a single crafted packet, and per-block CU ceilings (`MAX_BLOCK_UNITS`) and per-account CU ceilings still bound the blast radius to a slot at a time.

### Recommendation
Consider requiring a minimum `compute_unit_price` proportional to the (explicit or default) `compute_unit_limit` for transactions to be admitted into scheduling/forwarding priority above a floor, so that the "reward/cost" priority calculation in `calculate_priority` cannot be gamed by requesting large default CU grants at `compute_unit_price = 0`. Alternatively, lower the default per-instruction CU grant (`DEFAULT_INSTRUCTION_COMPUTE_UNIT_LIMIT`) so that unpriced transactions cannot claim large compute budgets by default, forcing users who need more compute to explicitly price it via `SetComputeUnitPrice`.

### Proof of Concept
Not independently verified end-to-end in this session (would require live cluster/bench harness); the code paths cited above show the mechanism (`calculate_default_compute_unit_limit` granting up to `1_400_000` CU with `compute_unit_price` defaulted to `0`, and `calculate_priority`'s `reward/cost` heuristic) that would need to be exercised with a burst of such transactions to demonstrate measurable leader-side degradation. This is the main open uncertainty in this analysis — I did not find or confirm an independent minimum-fee-per-CU admission check elsewhere in the ingestion/scheduling pipeline (e.g., in `banking_stage` or QUIC stream admission) that would fully close this gap; a Devin session with cluster/bench access would be needed to confirm whether existing stake-weighted QoS and vote-only/tip mechanisms sufficiently mitigate this in practice.

### Citations

**File:** compute-budget-instruction/src/compute_budget_instruction_details.rs (L130-134)
```rust
        let compute_unit_price = self
            .requested_compute_unit_price
            .map_or(0, |(_index, requested_compute_unit_price)| {
                requested_compute_unit_price
            });
```

**File:** compute-budget-instruction/src/compute_budget_instruction_details.rs (L196-219)
```rust
    fn calculate_default_compute_unit_limit(&self, feature_set: &FeatureSet) -> u32 {
        // evaluate if any builtin has migrated with feature_set
        let (num_migrated, num_not_migrated) = self
            .migrating_builtin_feature_counters
            .migrating_builtin
            .iter()
            .enumerate()
            .fold((0, 0), |(migrated, not_migrated), (index, count)| {
                if count.0 > 0 && feature_set.is_active(get_migration_feature_id(index)) {
                    (migrated + count.0, not_migrated)
                } else {
                    (migrated, not_migrated + count.0)
                }
            });

        u32::from(self.num_non_migratable_builtin_instructions.0)
            .saturating_add(u32::from(num_not_migrated))
            .saturating_mul(MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT)
            .saturating_add(
                u32::from(self.num_non_builtin_instructions.0)
                    .saturating_add(u32::from(num_migrated))
                    .saturating_mul(DEFAULT_INSTRUCTION_COMPUTE_UNIT_LIMIT),
            )
    }
```

**File:** program-runtime/src/execution_budget.rs (L26-34)
```rust
pub const MAX_COMPUTE_UNIT_LIMIT: u32 = 1_400_000;

/// Roughly 0.5us/page, where page is 32K; given roughly 15CU/us, the
/// default heap page cost = 0.5 * 15 ~= 8CU/page
pub const DEFAULT_HEAP_COST: u64 = 8;
pub const DEFAULT_INSTRUCTION_COMPUTE_UNIT_LIMIT: u32 = 200_000;
// SIMD-170 defines max CUs to be allocated for any builtin program instructions, that
// have not been migrated to sBPF programs.
pub const MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT: u32 = 3_000;
```

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

**File:** core/src/forwarding_stage.rs (L587-640)
```rust
/// Calculate priority for a transaction:
///
/// The priority is calculated as:
/// P = R / (1 + C)
/// where P is the priority, R is the reward,
/// and C is the cost towards block-limits.
///
/// Current minimum costs are on the order of several hundred,
/// so the denominator is effectively C, and the +1 is simply
/// to avoid any division by zero due to a bug - these costs
/// are estimate by the cost-model and are not direct
/// from user input. They should never be zero.
/// Any difference in the prioritization is negligible for
/// the current transaction costs.
fn calculate_priority(
    transaction: &RuntimeTransaction<SanitizedTransactionView<&[u8]>>,
    bank: &Bank,
) -> Option<u64> {
    let transaction_configuration = transaction
        .transaction_configuration(&bank.feature_set)
        .ok()?;

    // Manually estimate fee here since currently interface doesn't allow a on SVM type.
    // Doesn't need to be 100% accurate so long as close and consistent.
    let prioritization_fee = transaction_configuration.priority_fee_lamports;
    let signature_details = transaction.signature_details();
    let signature_fee = signature_details
        .total_signatures()
        .saturating_mul(bank.fee_structure().lamports_per_signature);
    let fee_details = FeeDetails::new(signature_fee, prioritization_fee);

    let reward = bank
        .calculate_reward_and_burn_fee_details(&CollectorFeeDetails::from(fee_details))
        .get_deposit();

    let cost = CostModel::estimate_cost(
        transaction,
        transaction.program_instructions_iter(),
        transaction.num_requested_write_locks(),
        &bank.feature_set,
    );

    // We need a multiplier here to avoid rounding down too aggressively.
    // For many transactions, the cost will be greater than the fees in terms of raw lamports.
    // For the purposes of calculating prioritization, we multiply the fees by a large number so that
    // the cost is a small fraction.
    // An offset of 1 is used in the denominator to explicitly avoid division by zero.
    const MULTIPLIER: u64 = 1_000_000;
    Some(
        MULTIPLIER
            .saturating_mul(reward)
            .wrapping_div(cost.sum().saturating_add(1)),
    )
}
```

**File:** cost-model/src/block_cost_limits.rs (L9-10)
```rust
/// Number of compute units for one signature verification.
pub const SIGNATURE_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 24;
```

**File:** cost-model/src/block_cost_limits.rs (L22-33)
```rust
/// Number of compute units that a block is allowed. A block's compute units are
/// accumulated by Transactions added to it; A transaction's compute units are
/// calculated by cost_model, based on transaction's signatures, write locks,
/// data size and built-in and SBF instructions.
pub const MAX_BLOCK_UNITS: u64 = MAX_BLOCK_UNITS_SIMD_0256;
pub const MAX_BLOCK_UNITS_SIMD_0256: u64 = 60_000_000;
pub const MAX_BLOCK_UNITS_SIMD_0286: u64 = 100_000_000;

/// Number of compute units that a writable account in a block is allowed. The
/// limit is to prevent too many transactions write to same account, therefore
/// reduce block's parallelism.
pub const MAX_WRITABLE_ACCOUNT_UNITS: u64 = 24_000_000;
```

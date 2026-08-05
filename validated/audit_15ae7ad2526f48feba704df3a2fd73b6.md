Audit Report

## Title
Precompile signature-count accounting trusts an unvalidated `data[0]` byte, decoupling declared CU/fee cost from real verification work - ([File: runtime-transaction/src/signature_details.rs])

## Summary
`get_num_signatures_in_instruction` reads a single, attacker-controlled byte (`instruction.data[0]`) and treats it as the authoritative signature count for secp256k1/ed25519/secp256r1 precompile instructions, feeding it into cost-model, fee, and forwarding-priority calculations before the real offset-based verifier in `agave_precompiles` ever validates that the instruction data can structurally support that count. [1](#0-0)  Because the real verifiers short-circuit with `PrecompileError::InvalidInstructionDataSize` as soon as `data.len()` is smaller than `count * OFFSETS_SIZE + DATA_START`, an attacker can declare up to 255 signatures with only 1–2 bytes of instruction data, causing the cost model to reserve/charge for up to ~1.7M CU of secp256k1 work per instruction while zero real verification work occurs. [2](#0-1) 

## Finding Description
`PrecompileSignatureDetailsBuilder::process_instruction` classifies instructions purely by `program_id` and unconditionally adds `get_num_signatures_in_instruction(instruction)`—`instruction.data.first().copied().unwrap_or(0)`—to the running signature counts, without any check that `instruction.data.len()` can actually hold that many offset structures. [3](#0-2) 

This unvalidated count is baked into `TransactionSignatureDetails` at sanitization time for both classic and transaction-view code paths: [4](#0-3) [5](#0-4) 

It is then consumed by:
- `CostModel::get_signature_cost`, which multiplies the declared counts by fixed per-signature CU costs (`SECP256K1_VERIFY_COST`, `ED25519_VERIFY_STRICT_COST`, `SECP256R1_VERIFY_COST`). [6](#0-5) [7](#0-6) 
- `fee::calculate_signature_fee`, which sums the same declared counts into the lamport fee. [8](#0-7) 
- Forwarding-stage priority estimation via `signature_details()`. [9](#0-8) 

Critically, `get_signature_cost` is called identically from both the *pre-execution estimate* (`CostModel::estimate_cost`/`calculate_cost`) and the *post-execution actual-cost* path (`CostModel::calculate_cost_for_executed_transaction`), since both funnel through the shared `calculate_transaction_cost` helper that always re-derives `signature_cost` from the transaction's cached (unvalidated) `signature_details`, regardless of `actual_programs_execution_cost`/`executed_units`. [10](#0-9)  This means even the *actual* cost recorded into the leader's `CostTracker.block_cost` / `cost_by_writable_accounts` after execution — via `get_transaction_costs` → `calculate_cost_for_executed_transaction` → `check_block_cost_limits` → `cost_tracker.try_add` — still includes the fabricated signature cost, unreconciled against the fact that the precompile's real `verify()` rejected the instruction outright with no actual cryptographic work performed. [11](#0-10) [12](#0-11) 

The real verifiers only validate `expected_data_size` internally, and only *after* this declared count has already been used for cost/fee/priority purposes: [13](#0-12) [14](#0-13)  There is no code path that clamps or recomputes `signature_cost` based on whether the declared count was structurally supportable by the data length, either before or after execution.

## Impact Explanation
This breaks the correspondence between "declared cost used for block-cost-limit accounting/scheduling/fee" and "actual work the runtime performs," corrupting the exact value: the `signature_cost` component of `TransactionCost`, and by extension `CostTracker.block_cost` / `cost_by_writable_accounts` (the resource bound the leader uses to cap CU consumption per block/account). By crafting minimal (1–2 byte) precompile instructions with `data[0]` set near 255, an attacker can claim near-maximal per-instruction signature-verification cost while consuming negligible real compute and negligible packet space — far below what a genuine transaction with that many real signature offsets could achieve within the ~1232-byte packet limit (255 real secp256k1 offset structures alone would require ~2805 bytes, exceeding the packet limit). Packing several such minimal instructions into one transaction lets an unprivileged sender inflate modeled block cost disproportionately to real work and packet-space paid, degrading the leader's ability to pack legitimate transactions once `MAX_BLOCK_UNITS`/`account_cost` limits are (falsely) approached — a non-RPC, low-cost mechanism for degrading block-packing throughput at the leader, matching a resource-bound/accounting-correctness impact.

## Likelihood Explanation
Constructing such an instruction requires no privileges, no valid cryptographic material, and no cooperation from other nodes — it is a plain, publicly-reachable transaction data field (`data[0]`) that any client can set to an arbitrary value. The mismatch between declared and real cost is deterministic and always reproducible, and the fee attacker must pay (`count * lamports_per_signature`) is modest relative to the amplification achieved in accounted CU per byte of packet space used.

## Recommendation
Validate, inside `get_num_signatures_in_instruction` (or its caller `PrecompileSignatureDetailsBuilder::process_instruction`), that `instruction.data.len()` is at least `count * OFFSETS_SERIALIZED_SIZE + DATA_START` for the relevant precompile (mirroring each verifier's own `expected_data_size` check) before trusting `data[0]` as the signature count for cost/fee accounting; otherwise clamp the counted signatures to the maximum count structurally supportable by the available data length. Additionally, consider reconciling the post-execution cost recorded into `CostTracker` with the true precompile verification outcome so that a transaction whose precompile rejects immediately with `InvalidInstructionDataSize` cannot retain an inflated `signature_cost` in the committed block-cost accounting.

## Proof of Concept
1. Build a transaction with a single instruction targeting `solana_sdk_ids::secp256k1_program::ID` with `data = vec![255]` and no other payload.
2. Sanitize the transaction; `get_precompile_signature_details` sets `num_secp256k1_instruction_signatures = 255` from `data[0]` alone. [1](#0-0) 
3. `CostModel::get_signature_cost`/`estimate_cost` computes `signature_cost ≈ 255 * SECP256K1_VERIFY_COST` (~1.7M CU-equivalent), used for scheduling/forwarding-priority before execution. [6](#0-5) 
4. On execution, `agave_precompiles::secp256k1::verify` computes `expected_data_size = 255 * SIGNATURE_OFFSETS_SERIALIZED_SIZE + 1`, finds `data.len() == 1` insufficient, and returns `PrecompileError::InvalidInstructionDataSize` immediately — no cryptographic work performed. [2](#0-1) 
5. After commit, `get_transaction_costs`/`calculate_cost_for_executed_transaction` still derives `signature_cost` from the same unvalidated `signature_details`, so the inflated cost is added to `CostTracker.block_cost`/`cost_by_writable_accounts` via `check_block_cost_limits`, despite the instruction performing no real verification work. [11](#0-10) [15](#0-14) 

A unit test comparing `CostModel::calculate_cost(&tx, ...).sum()` (or the post-execution equivalent) against the actual outcome of `agave_precompiles::secp256k1::verify(&data, ...)` for this crafted instruction would demonstrate the full decoupling between declared cost and real work.

### Citations

**File:** runtime-transaction/src/signature_details.rs (L29-53)
```rust
impl PrecompileSignatureDetailsBuilder {
    pub fn process_instruction(&mut self, program_id: &Pubkey, instruction: &SVMInstruction) {
        let program_id_index = instruction.program_id_index;
        match self.filter.is_signature(program_id_index, program_id) {
            ProgramIdStatus::NotSignature => {}
            ProgramIdStatus::Secp256k1 => {
                self.value.num_secp256k1_instruction_signatures = self
                    .value
                    .num_secp256k1_instruction_signatures
                    .wrapping_add(get_num_signatures_in_instruction(instruction));
            }
            ProgramIdStatus::Ed25519 => {
                self.value.num_ed25519_instruction_signatures = self
                    .value
                    .num_ed25519_instruction_signatures
                    .wrapping_add(get_num_signatures_in_instruction(instruction));
            }
            ProgramIdStatus::Secp256r1 => {
                self.value.num_secp256r1_instruction_signatures = self
                    .value
                    .num_secp256r1_instruction_signatures
                    .wrapping_add(get_num_signatures_in_instruction(instruction));
            }
        }
    }
```

**File:** runtime-transaction/src/signature_details.rs (L71-74)
```rust
#[inline]
fn get_num_signatures_in_instruction(instruction: &SVMInstruction) -> u64 {
    u64::from(instruction.data.first().copied().unwrap_or(0))
}
```

**File:** precompiles/src/secp256k1.rs (L28-43)
```rust
    if data.is_empty() {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let count = data[0] as usize;
    if count == 0 && data.len() > 1 {
        // count is zero but the instruction data indicates that is probably not
        // correct, fail the instruction to catch probable invalid secp256k1
        // instruction construction.
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let expected_data_size = count
        .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
        .saturating_add(1);
    if data.len() < expected_data_size {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
```

**File:** runtime-transaction/src/runtime_transaction/sdk_transactions.rs (L35-55)
```rust
        let InstructionMeta {
            precompile_signature_details,
            instruction_data_len,
        } = InstructionMeta::try_new(
            sanitized_versioned_tx
                .get_message()
                .program_instructions_iter()
                .map(|(program_id, ix)| (program_id, SVMInstruction::from(ix))),
        )?;
        let signature_details = TransactionSignatureDetails::new(
            u64::from(
                sanitized_versioned_tx
                    .get_message()
                    .message
                    .header()
                    .num_required_signatures,
            ),
            precompile_signature_details.num_secp256k1_instruction_signatures,
            precompile_signature_details.num_ed25519_instruction_signatures,
            precompile_signature_details.num_secp256r1_instruction_signatures,
        );
```

**File:** runtime-transaction/src/runtime_transaction/transaction_view.rs (L83-93)
```rust
    let InstructionMeta {
        precompile_signature_details,
        instruction_data_len,
    } = InstructionMeta::try_new(transaction.program_instructions_iter())?;

    let signature_details = TransactionSignatureDetails::new(
        u64::from(transaction.num_required_signatures()),
        precompile_signature_details.num_secp256k1_instruction_signatures,
        precompile_signature_details.num_ed25519_instruction_signatures,
        precompile_signature_details.num_secp256r1_instruction_signatures,
    );
```

**File:** cost-model/src/cost_model.rs (L35-77)
```rust
impl CostModel {
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

    // Calculate executed transaction CU cost, with actual execution and loaded accounts size
    // costs.
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

**File:** cost-model/src/cost_model.rs (L129-151)
```rust
    /// Returns signature details and the total signature cost
    fn get_signature_cost(transaction: &impl TransactionMeta) -> u64 {
        let signatures_count_detail = transaction.signature_details();

        signatures_count_detail
            .num_transaction_signatures()
            .saturating_mul(SIGNATURE_COST)
            .saturating_add(
                signatures_count_detail
                    .num_secp256k1_instruction_signatures()
                    .saturating_mul(SECP256K1_VERIFY_COST),
            )
            .saturating_add(
                signatures_count_detail
                    .num_ed25519_instruction_signatures()
                    .saturating_mul(ED25519_VERIFY_STRICT_COST),
            )
            .saturating_add(
                signatures_count_detail
                    .num_secp256r1_instruction_signatures()
                    .saturating_mul(SECP256R1_VERIFY_COST),
            )
    }
```

**File:** cost-model/src/block_cost_limits.rs (L9-16)
```rust
/// Number of compute units for one signature verification.
pub const SIGNATURE_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 24;
/// Number of compute units for one secp256k1 signature verification.
pub const SECP256K1_VERIFY_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 223;
/// Number of compute units for one ed25519 strict signature verification.
pub const ED25519_VERIFY_STRICT_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 80;
/// Number of compute units for one secp256r1 signature verification.
pub const SECP256R1_VERIFY_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 160;
```

**File:** fee/src/lib.rs (L42-56)
```rust
pub fn calculate_signature_fee(
    SignatureCounts {
        num_transaction_signatures,
        num_ed25519_signatures,
        num_secp256k1_signatures,
        num_secp256r1_signatures,
    }: SignatureCounts,
    lamports_per_signature: u64,
) -> u64 {
    let signature_count = num_transaction_signatures
        .saturating_add(num_ed25519_signatures)
        .saturating_add(num_secp256k1_signatures)
        .saturating_add(num_secp256r1_signatures);
    signature_count.saturating_mul(lamports_per_signature)
}
```

**File:** core/src/forwarding_stage.rs (L609-627)
```rust
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
```

**File:** runtime/src/transaction_execution.rs (L171-195)
```rust
// Get actual transaction execution costs from transaction commit results
fn get_transaction_costs<'a, Tx: TransactionWithMeta>(
    bank: &Bank,
    commit_results: &[TransactionCommitResult],
    sanitized_transactions: &'a [Tx],
) -> Vec<Option<TransactionCost<'a, Tx>>> {
    assert_eq!(sanitized_transactions.len(), commit_results.len());

    commit_results
        .iter()
        .zip(sanitized_transactions)
        .map(|(commit_result, tx)| {
            if let Ok(committed_tx) = commit_result {
                Some(CostModel::calculate_cost_for_executed_transaction(
                    tx,
                    committed_tx.executed_units,
                    committed_tx.loaded_account_stats.loaded_accounts_data_size,
                    &bank.feature_set,
                ))
            } else {
                None
            }
        })
        .collect()
}
```

**File:** cost-model/src/cost_tracker.rs (L312-336)
```rust
    // Returns the highest account cost for all write-lock accounts `TransactionCost` updated
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

    fn remove_transaction_cost(&mut self, tx_cost: &TransactionCost<impl TransactionWithMeta>) {
        let cost = tx_cost.sum();
        self.sub_transaction_execution_cost(tx_cost, cost);
        self.allocated_accounts_data_size -= tx_cost.allocated_accounts_data_size();
        self.transaction_count -= 1;
        self.transaction_signature_count -= tx_cost.num_transaction_signatures();
        self.secp256k1_instruction_signature_count -=
            tx_cost.num_secp256k1_instruction_signatures();
        self.ed25519_instruction_signature_count -= tx_cost.num_ed25519_instruction_signatures();
        self.secp256r1_instruction_signature_count -=
            tx_cost.num_secp256r1_instruction_signatures();
    }
```

**File:** precompiles/src/ed25519.rs (L16-29)
```rust
    if data.len() < SIGNATURE_OFFSETS_START {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let num_signatures = data[0] as usize;
    if num_signatures == 0 && data.len() > SIGNATURE_OFFSETS_START {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let expected_data_size = num_signatures
        .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
        .saturating_add(SIGNATURE_OFFSETS_START);
    // We do not check or use the byte at data[1]
    if data.len() < expected_data_size {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
```

**File:** precompiles/src/secp256r1.rs (L18-41)
```rust
pub fn verify(
    data: &[u8],
    instruction_datas: &[&[u8]],
    _feature_set: &FeatureSet,
) -> Result<(), PrecompileError> {
    if data.len() < SIGNATURE_OFFSETS_START {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let num_signatures = data[0] as usize;
    if num_signatures == 0 {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    if num_signatures > 8 {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }

    let expected_data_size = num_signatures
        .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
        .saturating_add(SIGNATURE_OFFSETS_START);

    // We do not check or use the byte at data[1]
    if data.len() < expected_data_size {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
```

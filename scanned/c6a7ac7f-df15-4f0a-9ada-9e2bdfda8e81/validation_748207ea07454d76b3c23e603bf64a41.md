### Title
Precompile signature-count accounting trusts an unvalidated `data[0]` byte, decoupling declared CU/fee cost from real verification work - ([File: runtime-transaction/src/signature_details.rs])

### Summary
`get_num_signatures_in_instruction` reads a single byte at a fixed position (`instruction.data[0]`) and treats it as the authoritative "number of signatures" for a secp256k1/ed25519/secp256r1 precompile instruction, without validating that the rest of the instruction data actually contains that many well-formed offset structures. [1](#0-0) 
This count is fed straight into the transaction cost model and fee calculation before the precompile's real ABI-like offset-based verifier ever runs, so a value that has no relationship to actual work performed is trusted for accounting purposes — the same class of bug as the `AllowedCalldataEnforcer` report, where a check on a fixed byte offset is assumed to reflect what the real decoder will use, but the real decoder (which resolves data through an indirect offset/pointer table) can completely diverge from that assumption.

### Finding Description
`PrecompileSignatureDetailsBuilder::process_instruction` classifies an instruction by `program_id` (secp256k1/ed25519/secp256r1) and then unconditionally adds `get_num_signatures_in_instruction(instruction)` to the running total, where that helper is just `instruction.data.first().copied().unwrap_or(0)`: [2](#0-1) [1](#0-0) 

No check is performed here that `instruction.data.len()` is large enough to actually contain that many `SecpSignatureOffsets` / `Ed25519SignatureOffsets` / `Secp256r1SignatureOffsets` structures. That validation only happens later, inside the *real* precompile verifiers, which compute `expected_data_size = count * OFFSETS_SIZE + DATA_START` and bail out with `PrecompileError::InvalidInstructionDataSize` if the data is too short: [3](#0-2) [4](#0-3) 

This mirrors exactly the report's structure: a "technical" fixed-offset read (`data[0]`) is used as if it faithfully represents what the real, more complex decoder (which follows an offsets/pointer indirection table into potentially different instructions, analogous to Solidity's ABI dynamic-type pointer indirection) will produce. The two can be made to diverge trivially by an attacker: set `data[0] = 255` and truncate the rest of the instruction to just that single byte (or a few bytes), so the "declared" signature count is huge while the actual instruction is malformed and will be rejected by `verify()` with `InvalidInstructionDataSize` before any real cryptographic work is done.

This corrupted count is consumed by:
- The compute-unit cost model, which multiplies it by fixed per-signature costs (`SECP256K1_VERIFY_COST`, `ED25519_VERIFY_STRICT_COST`, `SECP256R1_VERIFY_COST`) to build `signature_cost`: [5](#0-4) [6](#0-5) 
- The fee calculation path, which adds it into `num_transaction_signatures`-style totals to compute lamport fees: [7](#0-6) 
- Forwarding/priority estimation, which uses the same `signature_details()` to compute fee-per-cost priority before the transaction is executed: [8](#0-7) 

Because the byte that drives all of this is read without any correlation to whether the instruction can even be parsed by the real verifier, an attacker can construct a single-instruction transaction (`data = [255]`, no offsets, no signature/pubkey/message payload) that is guaranteed to fail `verify()` immediately, yet is estimated by the cost model as if it performed up to 255 secp256k1/ed25519/secp256r1 verifications (≈223 CU × 30 × 255 ≈ 1.7M CU per instruction for secp256k1 alone). A transaction can carry several such minimal instructions (each only 1–2 bytes of data plus a program-id account reference), multiplying the effect within the ~1232-byte packet size limit.

### Impact Explanation
This breaks the intended correspondence between "declared cost used for block-cost-limit accounting / scheduling" and "actual work the runtime will do." The cost model and block-cost-limit tracker (`MAX_BLOCK_UNITS`, `MAX_WRITABLE_ACCOUNT_UNITS`) are meant to bound leader compute-per-block based on genuine execution cost; if a cheap, near-instantly-failing transaction can be made to report a multi-million-CU cost, an attacker can exhaust a leader's modeled block budget or manipulate the forwarding-stage's `calculate_priority` fee/cost ratio with transactions that never do the claimed work. This is a non-RPC, low-cost mechanism for degrading block-packing/throughput at the leader — a resource-accounting integrity issue, not merely a UX quirk, since it lets an unprivileged sender's transaction data (an untrusted byte fully under attacker control) directly falsify a value multiple safety-critical subsystems (fee, cost model, forwarding priority) rely on being an honest reflection of the instruction's real signature-verification workload.

### Likelihood Explanation
Very easy to trigger: constructing a secp256k1/ed25519/secp256r1 instruction with `data[0]` set to a large value and no additional payload requires no special privileges, no cryptographic material, and no cooperation from other validators — it is a plain, unprivileged transaction crafted by any client. The mismatch is deterministic and always reproducible; no race conditions or timing dependencies are involved.

### Recommendation
`get_num_signatures_in_instruction` (or its caller) should validate that the instruction data is at least large enough to hold `count * OFFSETS_SERIALIZED_SIZE + DATA_START` bytes — the same `expected_data_size` check each precompile's `verify()` performs — before trusting `data[0]` as the signature count for fee/cost purposes. Alternatively, cap the counted signatures by what the data length can actually support, or clamp the derived cost by successful precompile verification when available (e.g., post-execution cost reconciliation), so the value used for block-cost accounting can never exceed the maximum instruction count that the corresponding data length could structurally support. Note: I was unable to fully confirm within available tool calls whether the runtime later reconciles this pre-execution estimated cost with post-execution actual cost for failed transactions in `cost_tracker.rs`/`consumer.rs`; this should be verified to determine whether the inflated reservation persists for the whole block or is corrected after execution.

### Proof of Concept
1. Build a transaction with one instruction targeting `solana_sdk_ids::secp256k1_program::ID` (or `ed25519_program`/`secp256r1_program`) with `data = vec![255]` (or `vec![255, 0]`) and no accounts.
2. `RuntimeTransaction::try_from` / `InstructionMeta::try_new` computes `precompile_signature_details.num_secp256k1_instruction_signatures = 255` via `get_precompile_signature_details`, purely from `data[0]`: [9](#0-8) 
3. `CostModel::get_signature_cost` multiplies this by `SECP256K1_VERIFY_COST` (223 CU-equivalent × 30), producing a signature_cost on the order of ~1.7M CU, used in `estimate_cost`/`calculate_priority` before execution: [10](#0-9) 
4. When the transaction actually reaches the precompile, `agave_precompiles::secp256k1::verify` computes `expected_data_size = 255 * SIGNATURE_OFFSETS_SERIALIZED_SIZE + 1`, sees `data.len() == 1 < expected_data_size`, and immediately returns `PrecompileError::InvalidInstructionDataSize` — no actual signature verification work occurs: [11](#0-10) 

The declared cost (used for scheduling/priority/block-limit accounting) and the real work performed (zero) are completely decoupled, demonstrating the broken invariant.

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

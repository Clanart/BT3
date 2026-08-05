## Title
Precompile signature verification consumes zero compute units, letting the static cost-model estimate diverge arbitrarily from real per-signature verification work - (File: `program-runtime/src/invoke_context.rs`, `cost-model/src/block_cost_limits.rs`)

### Summary
This is the same underlying bug class as the minievm precompile finding: an on-chain "precompile" performs real, potentially expensive verification work, but the system's cost/gas accounting for that work is decoupled from what is actually executed. In minievm, gas was charged *after* unpacking/verification instead of before, so failures were free. In Agave, the analog is stronger: the ed25519/secp256k1/secp256r1 precompiles are dispatched through `InvokeContext::process_precompile` [1](#0-0)  which never calls `compute_meter.consume_checked(..)` for the actual cryptographic verification, so the VM-level compute-unit meter records **zero** usage for precompile instructions regardless of how many/how expensive the signature checks are, as directly confirmed by the test `test_builtin_ix_precompiled` (VM execution consumes 0 CU, full builtin allocation refunded) [2](#0-1) . The only accounting for this work is a static, per-signature cost-model constant (`SECP256K1_VERIFY_COST`, `ED25519_VERIFY_STRICT_COST`, `SECP256R1_VERIFY_COST`) used purely for block-packing/fee estimation [3](#0-2) [4](#0-3) , not an actual metered budget enforced during execution.

### Finding Description
The precompile verify functions (`secp256k1::verify`, `ed25519::verify`, `secp256r1::verify`) perform real, non-trivial cryptographic work per declared signature — ECDSA recovery for secp256k1, EdDSA verification for ed25519, and full OpenSSL ECDSA verification (including big-number parsing and point validation) for secp256r1 [5](#0-4) . The number of signatures processed is taken directly from `data[0]` of the instruction, capped at 255 for secp256k1/ed25519 and 8 for secp256r1 [6](#0-5) [7](#0-6) .

Under SIMD-0159 (`move_precompile_verification_to_svm`), this verification is invoked from inside the SVM via `InvokeContext::process_precompile`, which pushes an instruction frame, calls the callback's `process_precompile`, and pops — with no compute-unit charge whatsoever tied to the actual verification cost [1](#0-0) . The `compute_units_consumed` value returned to the caller for this instruction is 0, as demonstrated by the cost-adjustment test, which shows the entire `MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT` reservation being refunded after executing a precompile instruction [2](#0-1) .

The only cost signal that exists for this work is the static `SIGNATURE_COST`/`SECP256K1_VERIFY_COST`/`ED25519_VERIFY_STRICT_COST`/`SECP256R1_VERIFY_COST` constants used by `CostModel::get_signature_cost` [4](#0-3) , which feed into `MAX_BLOCK_UNITS` block-packing decisions [8](#0-7)  and into the transaction fee (`calculate_signature_fee`) [9](#0-8) . These are fixed heuristic estimates (e.g., `SECP256R1_VERIFY_COST = 30 * 160 = 4800` cost-units), not measurements of real wall-clock/CPU cost of the actual OpenSSL EC point/BigNum operations, and they are never enforced as a hard per-instruction compute budget the way ordinary syscalls are (compare to `SyscallSecp256k1Recover`, which does call `compute_meter.consume_checked(cost)` for its single recovery [10](#0-9) ). Precompile instructions get none of that runtime enforcement — the cost model's number is advisory only for scheduling/fee, and the exact per-signature invariant "declared signature count == real verification cost charged to the compute meter" is broken because there is no compute-meter charge for precompiles at all.

### Impact Explanation
Because precompile verification bypasses per-instruction CU metering, a transaction can pack the maximum allowed number of secp256r1 (8 per instruction) or ed25519/secp256k1 (255 per instruction) signature checks across as many precompile instructions as fit in a transaction/block, and the actual wall-clock cost of validating them (OpenSSL big-number ECDSA math, repeated per signature) is bounded only by the static, possibly inaccurate cost-model constant used for block-packing — not by the real per-transaction `compute_unit_limit`, and not by any hard CU ceiling enforced at execution time. If the fixed cost-model estimate under-prices the true CPU cost of secp256r1/ed25519/secp256k1 verification relative to other instructions that are properly metered, validators/leaders can be induced to pack blocks that pass the `MAX_BLOCK_UNITS` cost-model check while taking substantially longer to actually execute (verify) than the model assumes — the exact "gas not charged for the real work performed" DOS pattern from the minievm report, applied to block-time/slot-time exhaustion rather than to a single call. This can degrade validator processing throughput / cause consensus-relevant slot-time pressure across the cluster since every validator must re-verify the same precompile instructions when replaying the block.

### Likelihood Explanation
Any unprivileged user can submit ordinary transactions containing secp256k1/ed25519/secp256r1 precompile instructions with the maximum signature counts; this requires no special access, no malicious validator assumption, and no leaked keys — it only requires knowledge of the public precompile instruction formats, which are well documented. The relevant code paths (`process_precompile`, the precompile `verify` functions, and the cost-model constants) are all reachable through normal transaction submission and were directly confirmed by the codebase's own test (`test_builtin_ix_precompiled`) showing zero CU consumption for precompile execution.

### Recommendation
Meter precompile verification against the compute-unit budget in proportion to real work performed (e.g., consume compute units per signature check as they're processed inside `process_precompile`/the `verify` functions, similar to how `SyscallSecp256k1Recover` charges `secp256k1_recover_cost` per recovery), rather than relying solely on the static, unmetered cost-model constants used for block-packing/fee estimation. Additionally, re-benchmark `SECP256K1_VERIFY_COST`, `ED25519_VERIFY_STRICT_COST`, and `SECP256R1_VERIFY_COST` against actual worst-case verification latency (particularly the OpenSSL-based secp256r1 path) to ensure the block-cost model cannot be gamed into approving blocks that take materially longer to replay than their modeled cost implies.

### Proof of Concept
Conceptual PoC (consistent with the codebase's own test harness):
1. Construct a transaction containing the maximum number of secp256r1-program instructions that fit under transaction size limits, each with `num_signatures = 8` and valid signature-offset headers pointing at bogus-but-well-formed signature/pubkey/message data so `secp256r1::verify` runs the full OpenSSL ECDSA verification path for all 8 signatures per instruction [11](#0-10) .
2. Submit many such transactions to fill a block up to `MAX_BLOCK_UNITS` as computed by `CostModel::get_signature_cost` [4](#0-3)  — the cost model believes this is a normally-priced block.
3. Observe (mirroring `test_builtin_ix_precompiled` [2](#0-1) ) that executing each precompile instruction consumes 0 CU from the meter, so the compute-budget mechanism provides no throttling, and the actual replay time of the block is driven purely by the real (unmetered) cost of the OpenSSL verification calls, which can exceed what the static cost-model constants assume.

Note: I was not able to fully verify, within the available tool budget, the exact numeric relationship between the current `SECP256R1_VERIFY_COST`/`ED25519_VERIFY_STRICT_COST` constants and measured wall-clock cost of the OpenSSL/ed25519-dalek verification calls in this codebase (no bench data comparing modeled cost-units to real microseconds was found), so the degree of under-pricing (if any) is not conclusively established from local evidence alone — this would need runtime benchmarking to confirm severity.

### Citations

**File:** program-runtime/src/invoke_context.rs (L616-631)
```rust
    /// Processes a precompile instruction
    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    fn process_precompile(
        &mut self,
        program_id: &Pubkey,
        instruction_data: &[u8],
        message_instruction_datas_iter: impl Iterator<Item = &'ix_data [u8]>,
    ) -> Result<(), InstructionError> {
        self.push()?;
        let instruction_datas: Vec<_> = message_instruction_datas_iter.collect();
        self.environment_config
            .epoch_stake_callback
            .process_precompile(program_id, instruction_data, instruction_datas)
            .map_err(InstructionError::from)
            .and(self.pop())
    }
```

**File:** core/tests/scheduler_cost_adjustment.rs (L381-402)
```rust
#[test]
fn test_builtin_ix_precompiled() {
    let mut test_setup = TestSetup::new();

    // single precompiled instruction
    // Cost model & Compute budget: reserve/allocate default CU for one builtin ix
    // VM Execution: consume 0 from CU-meter
    // Result: adjustment = 3_000
    let expected = TestResult {
        cost_adjustment: MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT as i64,
        execution_status: Ok(()),
    };
    assert_eq!(
        expected,
        test_setup.execute_test_transaction(&[Instruction::new_with_bincode(
            secp256k1_program::id(),
            &[0u8],
            // Add a dummy account to generate a unique transaction
            vec![AccountMeta::new_readonly(Pubkey::new_unique(), false)]
        )],)
    );
}
```

**File:** cost-model/src/block_cost_limits.rs (L9-20)
```rust
/// Number of compute units for one signature verification.
pub const SIGNATURE_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 24;
/// Number of compute units for one secp256k1 signature verification.
pub const SECP256K1_VERIFY_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 223;
/// Number of compute units for one ed25519 strict signature verification.
pub const ED25519_VERIFY_STRICT_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 80;
/// Number of compute units for one secp256r1 signature verification.
pub const SECP256R1_VERIFY_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 160;
/// Number of compute units for one write lock
pub const WRITE_LOCK_UNITS: u64 = COMPUTE_UNIT_TO_US_RATIO * 10;
/// Number of data bytes per compute units
pub const INSTRUCTION_DATA_BYTES_COST: u64 = 140 /*bytes per us*/ / COMPUTE_UNIT_TO_US_RATIO;
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

**File:** precompiles/src/secp256r1.rs (L26-139)
```rust
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

    // Parse half order from constant
    let half_order: BigNum =
        BigNum::from_slice(&SECP256R1_HALF_ORDER).map_err(|_| PrecompileError::InvalidSignature)?;

    // Parse order - 1 from constant
    let order_minus_one: BigNum = BigNum::from_slice(&SECP256R1_ORDER_MINUS_ONE)
        .map_err(|_| PrecompileError::InvalidSignature)?;

    // Create a BigNum for 1
    let one = BigNum::from_u32(1).map_err(|_| PrecompileError::InvalidSignature)?;

    // Define curve group
    let group = EcGroup::from_curve_name(Nid::X9_62_PRIME256V1)
        .map_err(|_| PrecompileError::InvalidSignature)?;
    let mut ctx = BigNumContext::new().map_err(|_| PrecompileError::InvalidSignature)?;

    for i in 0..num_signatures {
        let start = i
            .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
            .saturating_add(SIGNATURE_OFFSETS_START);

        // SAFETY:
        // - data[start..] is guaranteed to be >= size of Secp256r1SignatureOffsets
        // - Secp256r1SignatureOffsets is a POD type, so we can safely read it as an unaligned struct
        let offsets = unsafe {
            core::ptr::read_unaligned(data.as_ptr().add(start) as *const Secp256r1SignatureOffsets)
        };

        // Parse out signature
        let signature = get_data_slice(
            data,
            instruction_datas,
            offsets.signature_instruction_index,
            offsets.signature_offset,
            SIGNATURE_SERIALIZED_SIZE,
        )?;

        // Parse out pubkey
        let pubkey = get_data_slice(
            data,
            instruction_datas,
            offsets.public_key_instruction_index,
            offsets.public_key_offset,
            COMPRESSED_PUBKEY_SERIALIZED_SIZE,
        )?;

        // Parse out message
        let message = get_data_slice(
            data,
            instruction_datas,
            offsets.message_instruction_index,
            offsets.message_data_offset,
            offsets.message_data_size as usize,
        )?;

        let r_bignum = BigNum::from_slice(&signature[..FIELD_SIZE])
            .map_err(|_| PrecompileError::InvalidSignature)?;
        let s_bignum = BigNum::from_slice(&signature[FIELD_SIZE..])
            .map_err(|_| PrecompileError::InvalidSignature)?;

        // Check that the signature is generally in range
        let within_range = r_bignum >= one
            && r_bignum <= order_minus_one
            && s_bignum >= one
            && s_bignum <= half_order;

        if !within_range {
            return Err(PrecompileError::InvalidSignature);
        }

        // Create an ECDSA signature object from the ASN.1 integers
        let ecdsa_sig = openssl::ecdsa::EcdsaSig::from_private_components(r_bignum, s_bignum)
            .and_then(|sig| sig.to_der())
            .map_err(|_| PrecompileError::InvalidSignature)?;

        let public_key_point = EcPoint::from_bytes(&group, pubkey, &mut ctx)
            .map_err(|_| PrecompileError::InvalidPublicKey)?;
        let public_key = EcKey::from_public_key(&group, &public_key_point)
            .map_err(|_| PrecompileError::InvalidPublicKey)?;
        let public_key_as_pkey =
            PKey::from_ec_key(public_key).map_err(|_| PrecompileError::InvalidPublicKey)?;

        let mut verifier =
            Verifier::new(openssl::hash::MessageDigest::sha256(), &public_key_as_pkey)
                .map_err(|_| PrecompileError::InvalidSignature)?;
        verifier
            .update(message)
            .map_err(|_| PrecompileError::InvalidSignature)?;

        if !verifier
            .verify(&ecdsa_sig)
            .map_err(|_| PrecompileError::InvalidSignature)?
        {
            return Err(PrecompileError::InvalidSignature);
        }
    }
    Ok(())
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

**File:** fee/src/lib.rs (L112-124)
```rust
        // Pre-compile signatures.
        assert_eq!(
            calculate_signature_fee(
                SignatureCounts {
                    num_transaction_signatures: 1,
                    num_ed25519_signatures: 2,
                    num_secp256k1_signatures: 3,
                    num_secp256r1_signatures: 4,
                },
                LAMPORTS_PER_SIGNATURE,
            ),
            10 * LAMPORTS_PER_SIGNATURE
        );
```

**File:** syscalls/src/lib.rs (L913-926)
```rust
declare_builtin_function!(
    /// secp256k1_recover
    SyscallSecp256k1Recover,
    fn rust(
        invoke_context: &mut InvokeContext<'_, '_>,
        hash_addr: u64,
        recovery_id_val: u64,
        signature_addr: u64,
        result_addr: u64,
        _arg5: u64,
    ) -> Result<u64, Error> {
        let cost = invoke_context.get_execution_cost().secp256k1_recover_cost;
        invoke_context.compute_meter.consume_checked(cost)?;

```

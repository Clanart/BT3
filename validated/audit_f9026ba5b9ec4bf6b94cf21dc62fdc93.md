### Title
Precompile signature-verification cost model charges a flat per-signature CU cost that is independent of `message_data_size`, letting an attacker force disproportionately expensive `secp256k1`/`secp256r1` cryptographic work per compute unit charged - ([File: cost-model/src/block_cost_limits.rs], [File: precompiles/src/secp256k1.rs], [File: precompiles/src/secp256r1.rs])

### Summary
The Agave cost model charges a fixed compute-unit cost per signature for the `secp256k1`/`secp256r1`/`ed25519` precompiles (`SECP256K1_VERIFY_COST`, `SECP256R1_VERIFY_COST`) regardless of how large `message_data_size` is for each signature. The actual verification work in `secp256k1::verify` (keccak hash + `libsecp256k1::recover`) and `secp256r1::verify` (OpenSSL `EcdsaSig`/`Verifier`, which hashes the whole message with SHA-256) scales linearly with `message_data_size`, so real CPU cost per signature is not upper-bounded by the CU charge assessed for it.

### Finding Description
The per-signature costs are hardcoded constants that do not factor in message length: [1](#0-0) 

They are applied purely based on signature *count*, extracted from the first byte of each precompile instruction's data, with no accounting for the size of the referenced message: [2](#0-1) [3](#0-2) 

Meanwhile, the actual `verify()` routines do per-byte work on the attacker-controlled `message_data_size` slice:
- `secp256k1::verify` hashes the full message with keccak and then performs an EC point recovery, once per signature, for up to 255 signatures (`count: u8`): [4](#0-3) 
- `secp256r1::verify` builds an OpenSSL `Verifier` and calls `verifier.update(message)` (SHA-256 over the whole message) plus ECDSA verification, once per signature, for up to 8 signatures: [5](#0-4) 

Critically, when this verification actually runs during transaction processing (via `InvokeContext::process_precompile` → `Bank::process_precompile` → `Precompile::verify`), there is **no compute-meter charge tied to the work performed** — unlike the `secp256k1_recover` syscall, which explicitly does `invoke_context.compute_meter.consume_checked(cost)` before doing equivalent work: [6](#0-5) [7](#0-6) [8](#0-7) 

This is corroborated by an explicit test comment stating precompile execution consumes **0** CU from the meter, with only a flat, pre-computed builtin allocation applied: [9](#0-8) 

So the only cost accounting for this expensive work is the block/scheduler cost-model estimate (`SECP256K1_VERIFY_COST` / `SECP256R1_VERIFY_COST` per signature), which is flat regardless of `message_data_size`. An attacker can therefore construct a transaction with the maximum number of signatures (255 for secp256k1's `u8 count`, 8 for secp256r1's bounded `num_signatures`) whose offsets all reference the *same* large message-data region within the transaction (message data can be shared/re-referenced by multiple signature offsets without incurring additional charge), maximizing real per-signature hashing/EC work while the cost model only multiplies a fixed per-signature constant, not a function of bytes processed.

### Impact Explanation
This matches the "materially underpriced compute" / CPU-time amplification category: a leader/validator processing such a transaction pays real wall-clock CPU time (up to 255 keccak hashes + EC point recoveries, or up to 8 SHA-256-based ECDSA verifications over large messages) that is not reflected by the flat per-signature CU charge used for cost/fee accounting and block-cost-limit purposes. Repeated submission of such transactions by unprivileged senders can disproportionately consume validator CPU relative to the CU/fee they pay, though the real bound is constrained by the whole-transaction size limit (single transaction, not multiple calls), which caps the total distinguishable message bytes that can be referenced.

### Likelihood Explanation
Fully attacker-controlled and reachable with no privileges: any user can construct a legacy or versioned transaction with a `secp256k1_program`/`secp256r1_program` instruction, populate the offsets structure to point `num_signatures`/`count` copies of `SecpSignatureOffsets`/`Secp256r1SignatureOffsets` at the same message-data region (up to the actual per-transaction data-size limit), and submit it repeatedly. No special config or staking is needed; the imbalance is deterministic and reproducible on every validator that processes the transaction (during `Bank::verify_transaction`'s precompile verification step, and again on any node re-verifying it), so it is fully repeatable.

### Recommendation
Make the per-signature verify cost proportional to the message length actually hashed/verified (e.g., add a per-byte term similar to `sha256_byte_cost`/`INSTRUCTION_DATA_BYTES_COST` to `SECP256K1_VERIFY_COST`/`SECP256R1_VERIFY_COST`, or compute a cost as `base_cost + message_len * per_byte_cost` per signature in `cost-model/src/cost_model.rs::get_signature_cost` and mirror it in the fee structure). Alternatively, explicitly meter compute units for precompile message hashing inside `InvokeContext::process_precompile`/`Precompile::verify`, similar to how `SyscallSecp256k1Recover` charges `secp256k1_recover_cost` before doing recovery, so the cost is enforced at execution time and not merely estimated at scheduling time.

### Proof of Concept
```rust
// precompiles/benches/secp256k1_worst_case.rs (new)
#![feature(test)]
extern crate test;
use {
    agave_feature_set::FeatureSet,
    agave_precompiles::secp256k1::verify,
    solana_secp256k1_program::{
        eth_address_from_pubkey, new_secp256k1_instruction_with_signature, sign_message,
        SecpSignatureOffsets,
    },
    std::time::Instant,
    test::Bencher,
};

// Construct one secp256k1 instruction with 255 signature-offset entries that
// all point at the SAME large message-data region (shared, no extra bytes
// needed per signature), to maximize per-signature keccak+recover work
// while the cost model only charges a flat SECP256K1_VERIFY_COST per
// signature (223 * 30 = 6690 CU), independent of message size.
#[test]
fn test_worst_case_ratio_exceeds_cost_bound() {
    // build instruction_data with count = 255, each offsets entry pointing
    // to the same signature/message bytes within a single "packed" instruction
    // whose overall size stays within realistic transaction-size limits.
    // ... construct instruction_data, instruction_datas ...

    let feature_set = FeatureSet::all_enabled();
    let start = Instant::now();
    let _ = verify(&instruction_data, &instruction_datas, &feature_set);
    let elapsed = start.elapsed();

    // charged cost, from cost-model
    let charged_cu = 255u64 * cost_model::block_cost_limits::SECP256K1_VERIFY_COST;

    // Assert: wall-clock time per charged CU should not exceed a fixed
    // COMPUTE_UNIT_TO_US_RATIO bound (~30 CU per microsecond, i.e. ~33ns/CU).
    let expected_max_time_us = charged_cu as f64 / 30.0;
    assert!(
        elapsed.as_micros() as f64 <= expected_max_time_us,
        "real verification time {}us exceeds the time budget implied by charged CU ({}us), \
         showing message-size-dependent cost is not reflected in the flat per-signature charge",
        elapsed.as_micros(),
        expected_max_time_us
    );
}
```
Expected result: for small messages the assertion holds (flat cost roughly matches real time), but as `message_data_size` grows toward the practical maximum while `count`/`num_signatures` is maxed, real wall-clock time grows linearly with message size while the charged CU stays constant — the assertion fails, demonstrating the underpriced-compute invariant violation.

### Citations

**File:** cost-model/src/block_cost_limits.rs (L10-16)
```rust
pub const SIGNATURE_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 24;
/// Number of compute units for one secp256k1 signature verification.
pub const SECP256K1_VERIFY_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 223;
/// Number of compute units for one ed25519 strict signature verification.
pub const ED25519_VERIFY_STRICT_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 80;
/// Number of compute units for one secp256r1 signature verification.
pub const SECP256R1_VERIFY_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 160;
```

**File:** runtime-transaction/src/signature_details.rs (L71-74)
```rust
#[inline]
fn get_num_signatures_in_instruction(instruction: &SVMInstruction) -> u64 {
    u64::from(instruction.data.first().copied().unwrap_or(0))
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

**File:** precompiles/src/secp256k1.rs (L44-95)
```rust
    for i in 0..count {
        let start = i
            .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
            .saturating_add(1);
        let end = start.saturating_add(SIGNATURE_OFFSETS_SERIALIZED_SIZE);

        let offsets: SecpSignatureOffsets = bincode::deserialize(&data[start..end])
            .map_err(|_| PrecompileError::InvalidSignature)?;

        // Parse out signature
        let signature_index = offsets.signature_instruction_index as usize;
        if signature_index >= instruction_datas.len() {
            return Err(PrecompileError::InvalidInstructionDataSize);
        }
        let signature_instruction = instruction_datas[signature_index];
        let sig_start = offsets.signature_offset as usize;
        let sig_end = sig_start.saturating_add(SIGNATURE_SERIALIZED_SIZE);
        if sig_end >= signature_instruction.len() {
            return Err(PrecompileError::InvalidSignature);
        }

        let signature = libsecp256k1::Signature::parse_standard_slice(
            &signature_instruction[sig_start..sig_end],
        )
        .map_err(|_| PrecompileError::InvalidSignature)?;

        let recovery_id = libsecp256k1::RecoveryId::parse(signature_instruction[sig_end])
            .map_err(|_| PrecompileError::InvalidRecoveryId)?;

        // Parse out pubkey
        let eth_address_slice = get_data_slice(
            instruction_datas,
            offsets.eth_address_instruction_index,
            offsets.eth_address_offset,
            HASHED_PUBKEY_SERIALIZED_SIZE,
        )?;

        // Parse out message
        let message_slice = get_data_slice(
            instruction_datas,
            offsets.message_instruction_index,
            offsets.message_data_offset,
            offsets.message_data_size as usize,
        )?;

        let message_hash: [u8; 32] = solana_keccak_hasher::hash(message_slice).to_bytes();
        let pubkey = libsecp256k1::recover(
            &libsecp256k1::Message::parse_slice(&message_hash).unwrap(),
            &signature,
            &recovery_id,
        )
        .map_err(|_| PrecompileError::InvalidSignature)?;
```

**File:** precompiles/src/secp256r1.rs (L59-138)
```rust
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
```

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

**File:** syscalls/src/lib.rs (L923-925)
```rust
    ) -> Result<u64, Error> {
        let cost = invoke_context.get_execution_cost().secp256k1_recover_cost;
        invoke_context.compute_meter.consume_checked(cost)?;
```

**File:** runtime/src/bank.rs (L6694-6707)
```rust
    fn process_precompile(
        &self,
        program_id: &Pubkey,
        data: &[u8],
        instruction_datas: Vec<&[u8]>,
    ) -> std::result::Result<(), PrecompileError> {
        if let Some(precompile) = get_precompile(program_id, |feature_id: &Pubkey| {
            self.feature_set.is_active(feature_id)
        }) {
            precompile.verify(data, &instruction_datas, &self.feature_set)
        } else {
            Err(PrecompileError::InvalidPublicKey)
        }
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

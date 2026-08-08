## Title
Precompile signature verification (ed25519/secp256k1) charges a fixed builtin cost while the actual number of expensive EC signature checks is attacker-controlled up to 255 — materially underpriced compute - (File: `precompiles/src/ed25519.rs`, `precompiles/src/secp256k1.rs`, `cost-model/src/cost_model.rs`)

### Summary
`agave_precompiles::ed25519::verify` and `agave_precompiles::secp256k1::verify` each loop over `num_signatures` (a caller-controlled `u8`, i.e. up to 255) and perform a full elliptic-curve signature verification per iteration [1](#0-0) [2](#0-1) . Unlike normal BPF program execution, this verification runs during `InvokeContext::process_precompile` and is **not metered against the compute budget** — the cost-model test explicitly documents that a precompiled instruction execution "consume[s] 0 from CU-meter" while cost-model/scheduling still allocate only the fixed `MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT` (3,000 CU) [3](#0-2) . This is directly analogous to the referenced bug class: a caller-controlled unbounded/high-cardinality loop over cheaply-obtained inputs (`_feeReceivers` ↔ `num_signatures`) that performs expensive work per element while the enclosing metering treats the whole operation as cheap and fixed-cost.

### Finding Description
`process_message` routes any instruction whose program id matches a precompile straight to `process_precompile`, bypassing normal `process_instruction`/compute-metering path used for BPF and other builtin programs [4](#0-3) . `process_precompile` simply calls into the environment/epoch-stake callback's precompile verification and does not touch `self.compute_meter` at all [5](#0-4) , in contrast to `process_executable_chain` for ordinary builtins, which explicitly measures and requires non-zero CU consumption (`BuiltinProgramsMustConsumeComputeUnits`) [6](#0-5) .

Inside `ed25519::verify` and `secp256k1::verify`, the number of signature checks performed is `data[0]` (a `u8`), so up to 255 checks can be requested in a single instruction, each requiring a full EC signature verification (`ed25519_dalek::PublicKey::verify_strict`, or `libsecp256k1::recover` + Keccak hash) [7](#0-6) [8](#0-7) . There is no cap on `num_signatures` for these two precompiles (unlike `secp256r1::verify`, which explicitly rejects `num_signatures > 8` [9](#0-8) ).

The cost model / block-cost accounting for a precompiled instruction charges only the fixed builtin allocation (`MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT`, 3,000 CU) irrespective of `num_signatures`, as demonstrated by the dedicated test `test_builtin_ix_precompiled` which asserts cost adjustment equals the full 3,000 CU allocation for a precompiled instruction that consumes 0 CU during execution [3](#0-2) . `get_precompile_signature_details`, which is the only place that actually reads and totals `num_signatures` per instruction, is used for transaction/packet-level signature-count checks (`get_num_signatures_in_instruction`) rather than compute-unit charging [10](#0-9) .

The net effect is the same bug class as the report: an attacker-controlled iteration count (`num_signatures`, up to 255, analogous to `_feeReceivers.length`) drives real, expensive work (EC signature verification, analogous to ETH transfers to each receiver) that is not reflected in the resource accounting used to price/limit the operation (fixed 3,000 CU builtin allocation, analogous to unmetered gas).

### Impact Explanation
Because compute-unit accounting for precompiles is fixed regardless of `num_signatures`, a single transaction/instruction can force validators to perform up to 255 real elliptic-curve signature verifications (ed25519 or secp256k1 recover+hash) while being billed and scheduled as if it were a trivial fixed-cost builtin instruction. At scale (many such transactions packed into a block, each still counting only 3,000 CU of the block compute budget), this allows an attacker to consume much more real CPU/verification time per unit of "priced" compute than the cost model assumes, degrading validator performance without a correspondingly higher fee/priority cost — a materially underpriced compute condition reachable by any unprivileged transaction sender.

### Likelihood Explanation
This requires no privileged access, no special program deployment, and no CPI depth — a single top-level transaction instruction targeting the `secp256k1_program` or `ed25519_program` with a crafted `num_signatures` byte and matching offsets/instruction data is sufficient to trigger 255 EC verifications for a flat 3,000 CU-equivalent budget charge, making the condition trivially and repeatedly reachable by any user submitting transactions.

### Recommendation
Meter precompile verification against the compute budget proportionally to `num_signatures` (as is already done for `secp256r1`'s explicit cap), and/or cap `num_signatures` for `ed25519` and `secp256k1` similarly to `secp256r1`'s `num_signatures > 8` check in `precompiles/src/secp256r1.rs`. Alternatively, incorporate `num_signatures` from `get_precompile_signature_details` into the cost model's per-instruction execution-cost calculation (`cost-model/src/cost_model.rs`) so that block-cost/CU allocation for precompiled instructions scales with the actual number of signature checks requested, rather than using the fixed `MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT`.

### Proof of Concept
1. Construct a transaction with a single `secp256k1_program` (or `ed25519_program`) instruction whose data begins with `count = 255` (`data[0] = 255`), followed by 255 valid `SecpSignatureOffsets`/`Ed25519SignatureOffsets` entries all pointing at a single small signature/pubkey/message payload appended to the same instruction data (as done in `precompiles/src/secp256k1.rs` tests, e.g. `test_malleability`, generalized to 255 entries) [11](#0-10) .
2. Submit the transaction; `InvokeContext::process_message` detects the precompile program id and calls `process_precompile`, which calls `secp256k1::verify`, performing 255 `libsecp256k1::recover` + Keccak-256 operations [4](#0-3) [8](#0-7) .
3. Compare against `test_builtin_ix_precompiled`, which shows a single precompiled instruction (with a trivial `count = 0`) is priced identically at the flat `MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT` and consumes 0 CU from the meter during execution regardless of the verification work performed [3](#0-2)  — demonstrating that the cost charged does not scale with `num_signatures`, confirming the underpricing.

### Citations

**File:** precompiles/src/ed25519.rs (L19-77)
```rust
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
    for i in 0..num_signatures {
        let start = i
            .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
            .saturating_add(SIGNATURE_OFFSETS_START);

        // SAFETY:
        // - data[start..] is guaranteed to be >= size of Ed25519SignatureOffsets
        // - Ed25519SignatureOffsets is a POD type, so we can safely read it as an unaligned struct
        let offsets = unsafe {
            core::ptr::read_unaligned(data.as_ptr().add(start) as *const Ed25519SignatureOffsets)
        };

        // Parse out signature
        let signature = get_data_slice(
            data,
            instruction_datas,
            offsets.signature_instruction_index,
            offsets.signature_offset,
            SIGNATURE_SERIALIZED_SIZE,
        )?;

        let signature =
            Signature::from_bytes(signature).map_err(|_| PrecompileError::InvalidSignature)?;

        // Parse out pubkey
        let pubkey = get_data_slice(
            data,
            instruction_datas,
            offsets.public_key_instruction_index,
            offsets.public_key_offset,
            PUBKEY_SERIALIZED_SIZE,
        )?;

        let publickey = ed25519_dalek::PublicKey::from_bytes(pubkey)
            .map_err(|_| PrecompileError::InvalidPublicKey)?;

        // Parse out message
        let message = get_data_slice(
            data,
            instruction_datas,
            offsets.message_instruction_index,
            offsets.message_data_offset,
            offsets.message_data_size as usize,
        )?;
        publickey
            .verify_strict(message, &signature)
            .map_err(|_| PrecompileError::InvalidSignature)?;
    }
```

**File:** precompiles/src/secp256k1.rs (L31-102)
```rust
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
        let eth_address = eth_address_from_pubkey(&pubkey.serialize()[1..].try_into().unwrap());

        if eth_address_slice != eth_address {
            return Err(PrecompileError::InvalidSignature);
        }
    }
    Ok(())
```

**File:** precompiles/src/secp256k1.rs (L343-414)
```rust
    // Signatures are malleable.
    #[test]
    fn test_malleability() {
        agave_logger::setup();

        let secret_bytes: [u8; 32] = rand::random();
        let secret_key = libsecp256k1::SecretKey::parse(&secret_bytes).unwrap();
        let public_key = libsecp256k1::PublicKey::from_secret_key(&secret_key);
        let eth_address = eth_address_from_pubkey(&public_key.serialize()[1..].try_into().unwrap());

        let message = b"hello";
        let message_hash = {
            let mut hasher = keccak::Hasher::default();
            hasher.hash(message);
            hasher.result()
        };

        let secp_message = libsecp256k1::Message::parse(message_hash.as_bytes());
        let (signature, recovery_id) = libsecp256k1::sign(&secp_message, &secret_key);

        // Flip the S value in the signature to make a different but valid signature.
        let mut alt_signature = signature;
        alt_signature.s = -alt_signature.s;
        let alt_recovery_id = libsecp256k1::RecoveryId::parse(recovery_id.serialize() ^ 1).unwrap();

        let mut data: Vec<u8> = vec![];
        let mut both_offsets = vec![];

        // Verify both signatures of the same message.
        let sigs = [(signature, recovery_id), (alt_signature, alt_recovery_id)];
        for (signature, recovery_id) in sigs.iter() {
            let signature_offset = data.len();
            data.extend(signature.serialize());
            data.push(recovery_id.serialize());
            let eth_address_offset = data.len();
            data.extend(eth_address);
            let message_data_offset = data.len();
            data.extend(message);

            let data_start = 1 + SIGNATURE_OFFSETS_SERIALIZED_SIZE * 2;

            let offsets = SecpSignatureOffsets {
                signature_offset: (signature_offset + data_start) as u16,
                signature_instruction_index: 0,
                eth_address_offset: (eth_address_offset + data_start) as u16,
                eth_address_instruction_index: 0,
                message_data_offset: (message_data_offset + data_start) as u16,
                message_data_size: message.len() as u16,
                message_instruction_index: 0,
            };

            both_offsets.push(offsets);
        }

        let mut instruction_data: Vec<u8> = vec![2];

        for offsets in both_offsets {
            let offsets = bincode::serialize(&offsets).unwrap();
            instruction_data.extend(offsets);
        }

        instruction_data.extend(data);

        test_verify_with_alignment(
            verify,
            &instruction_data,
            &[&instruction_data],
            &FeatureSet::all_enabled(),
        )
        .unwrap();
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

**File:** program-runtime/src/invoke_context.rs (L511-524)
```rust
        for (top_level_instruction_index, (program_id, instruction)) in
            message.program_instructions_iter().enumerate()
        {
            let mut compute_units_consumed = 0;
            let (result, process_instruction_us) = measure_us!({
                if self.is_precompile(program_id) {
                    self.process_precompile(
                        program_id,
                        instruction.data,
                        message.instructions_iter().map(|ix| ix.data),
                    )
                } else {
                    self.process_instruction(&mut compute_units_consumed, execute_timings)
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

**File:** program-runtime/src/invoke_context.rs (L724-729)
```rust
        let post_remaining_units = self.get_remaining();
        *compute_units_consumed = pre_remaining_units.saturating_sub(post_remaining_units);

        if builtin_id == program_id && result.is_ok() && *compute_units_consumed == 0 {
            return Err(InstructionError::BuiltinProgramsMustConsumeComputeUnits);
        }
```

**File:** precompiles/src/secp256r1.rs (L26-32)
```rust
    let num_signatures = data[0] as usize;
    if num_signatures == 0 {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    if num_signatures > 8 {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
```

**File:** runtime-transaction/src/signature_details.rs (L60-74)
```rust
/// Get transaction signature details.
pub fn get_precompile_signature_details<'a>(
    instructions: impl Iterator<Item = (&'a Pubkey, SVMInstruction<'a>)>,
) -> PrecompileSignatureDetails {
    let mut builder = PrecompileSignatureDetailsBuilder::default();
    for (program_id, instruction) in instructions {
        builder.process_instruction(program_id, &instruction);
    }
    builder.build()
}

#[inline]
fn get_num_signatures_in_instruction(instruction: &SVMInstruction) -> u64 {
    u64::from(instruction.data.first().copied().unwrap_or(0))
}
```

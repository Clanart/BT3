### Title
Precompile signature-verification cost is charged at a flat, undersized rate that is decoupled from the runtime's own cost of the equivalent cryptographic operation — ([File: cost-model/src/block_cost_limits.rs], [File: precompiles/src/secp256k1.rs], [File: precompiles/src/ed25519.rs], [File: program-runtime/src/execution_budget.rs])

### Summary
The report's underlying pattern is: an unprivileged actor can make the system perform expensive computational work whose *accounted* cost does not match its *actual* cost, letting that actor "steal" processing resources cheaply (gas griefing / gas theft via a chosen callee that does disproportionate work). The closest Agave analog is the accounting split between (a) the fixed, size-independent, per-signature cost the cost model charges for `secp256k1_program`, `ed25519_program`, and `secp256r1_program` instructions, and (b) the actual CPU cost of performing that cryptography, which is not metered by the SVM's compute meter at all and which the runtime's *own* cost table already prices much higher for the equivalent operation.

### Finding Description
Precompile instructions are verified by `precompiles::verify_if_precompile` / the individual `verify` functions in `precompiles/src/secp256k1.rs` and `precompiles/src/ed25519.rs`, which perform real ECDSA recovery / Ed25519 `verify_strict` calls chosen entirely by the transaction's author (any unprivileged fee payer): [1](#0-0) [2](#0-1) 

The compute-unit *cost model* charges these instructions a flat, size-independent per-signature price: [3](#0-2) 

but a test in `core/tests/scheduler_cost_adjustment.rs` confirms that at actual VM execution time, precompile instructions consume **zero** units from the runtime's compute meter — the entire cost accounting for them lives only in the static, flat cost-model estimate, not in the transaction's compute budget: [4](#0-3) 

Compare this fixed cost-model estimate to the runtime's own internal price for the equivalent cryptographic primitive when invoked as an in-VM syscall (`secp256k1_recover`), which is nearly 4x higher than the flat precompile-instruction cost: [5](#0-4) 

This is structurally the same broken invariant as the external report: an unprivileged party (any transaction sender/"delegator") controls a call whose accounted "gas" price does not track its real work, and the actual cost-bearing party (validators processing/replaying the block) has no dial to stop it beyond the coarse, mis-priced cost-model estimate. Unlike other cryptographic syscalls in the same cost table (e.g. `sha256_byte_cost`, `curve25519_edwards_*`), precompile-instruction pricing has no per-byte/per-work term, and it is not enforced through the compute-unit meter that governs and bounds a transaction's actual execution — it's only used for coarse block-cost-limit bookkeeping.

### Impact Explanation
Because the real cryptographic work (secp256k1 recovery, Ed25519/secp256r1 verification, plus keccak/message hashing over attacker-chosen message slices from arbitrary other instructions in the same transaction) is not gated by the compute-unit meter and is charged at a rate lower than the runtime's own internal valuation of the same operation, an unprivileged transaction sender can pack many precompile-signature instructions into transactions/blocks whose declared cost-model footprint understates the real CPU cost imposed on every validator that must verify and replay that block. This is a non-RPC, remote resource-exhaustion vector against the transaction-verification/blockstore-replay path (`runtime/src/bank.rs::verify_transaction_with_serialized_message`, `ledger/src/blockstore_processor.rs::confirm_slot`) driven purely by ordinary, fee-paying, unprivileged transactions — no malicious validator, peer, or trusted-process assumption is required.

### Likelihood Explanation
Moderate-to-low confidence/likelihood as a standalone critical bug: the discrepancy is real and demonstrable in the cost tables and tests found, but the actual per-operation CPU cost of secp256k1/ed25519/secp256r1 verification is bounded (microseconds per signature) and the number of signatures per instruction/transaction is itself bounded by instruction/transaction size limits (`MAX_INSTRUCTION_TRACE_LENGTH`, `PACKET_DATA_SIZE`/`v1::MAX_TRANSACTION_SIZE`), which caps the amplification factor achievable in a single transaction. I was not able to fully trace, within the available tool budget, whether `verify_if_precompile` is actually invoked in the live (non-conformance) transaction-processing path used by `banking_stage`/`blockstore_processor`, or only in the `agave-unstable-api`-gated conformance harness and `TransactionVerificationMode::HashAndVerifyPrecompiles` code paths shown; this materially affects how exploitable the mismatch is in production. This uncertainty should be resolved before treating this as a confirmed, high-severity finding.

### Recommendation
- Confirm and, if necessary, wire precompile verification cost into the transaction's compute-unit meter (so `invoke_context.compute_meter.consume_checked` is charged for the real crypto work), rather than leaving it purely as a flat, block-cost-limit estimate.
- Align `SECP256K1_VERIFY_COST` / `ED25519_VERIFY_STRICT_COST` / `SECP256R1_VERIFY_COST` in `cost-model/src/block_cost_limits.rs` with the runtime's own cost figures for equivalent operations (e.g. `secp256k1_recover_cost`) and add a per-byte term for the hashed/verified message, mirroring how `sha256_byte_cost` is handled elsewhere in the same cost table.
- Verify (with full source access) exactly where `verify_if_precompile` is invoked in the production banking/replay pipeline and whether its cost is reflected in per-slot leader scheduling limits before block inclusion, not just in post-hoc block-cost accounting.

### Proof of Concept
Not executable from the indexed code alone — the analysis is derived from static comparison of the cost tables and unit tests referenced above (`cost-model/src/block_cost_limits.rs`, `program-runtime/src/execution_budget.rs`, `core/tests/scheduler_cost_adjustment.rs::test_builtin_ix_precompiled`). A concrete PoC would need to: (1) construct a transaction containing multiple `secp256k1_program`/`ed25519_program` instructions with the maximum allowed signature count referencing large message slices from sibling instructions, (2) measure actual validator-side CPU time to verify/replay such a block versus the cost-model-estimated compute units it consumes, and (3) confirm whether this path is reachable via `banking_stage`/`blockstore_processor`. This last confirmation step could not be completed within the current investigation and is flagged as an open item; a Devin session with full repository/build access would be needed to trace the call graph precisely and benchmark the actual CPU cost.

### Citations

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

**File:** precompiles/src/ed25519.rs (L30-77)
```rust
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

**File:** cost-model/src/block_cost_limits.rs (L8-16)
```rust
pub const COMPUTE_UNIT_TO_US_RATIO: u64 = 30;
/// Number of compute units for one signature verification.
pub const SIGNATURE_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 24;
/// Number of compute units for one secp256k1 signature verification.
pub const SECP256K1_VERIFY_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 223;
/// Number of compute units for one ed25519 strict signature verification.
pub const ED25519_VERIFY_STRICT_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 80;
/// Number of compute units for one secp256r1 signature verification.
pub const SECP256R1_VERIFY_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 160;
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

**File:** program-runtime/src/execution_budget.rs (L207-219)
```rust
impl Default for SVMTransactionExecutionCost {
    fn default() -> Self {
        SVMTransactionExecutionCost {
            log_64_units: 100,
            create_program_address_units: 1500,
            invoke_units: DEFAULT_INVOCATION_COST,
            sha256_base_cost: 85,
            sha256_byte_cost: 1,
            log_pubkey_units: 100,
            cpi_bytes_per_unit: 250, // ~50MB at 200,000 units
            sysvar_base_cost: 100,
            secp256k1_recover_cost: 25_000,
            syscall_base_cost: 100,
```

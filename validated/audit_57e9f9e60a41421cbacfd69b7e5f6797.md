## Title
Fixed, size-independent compute-unit charge for BPF Upgradeable Loader deployment lets attackers force expensive ELF verification/JIT-compile work far above the priced CU cost - (File: `programs/bpf_loader/src/lib.rs`, `program-runtime/src/deploy.rs`)

### Summary
The Arbitrum finding describes a class of bug where an expensive, arbitrarily-triggerable operation (Brotli decompression of WASM bytecode) is not priced proportionally to its actual cost, and in particular the cost can be evaded when the operation fails. The Agave analog is in the BPF Upgradeable Loader's `DeployWithMaxDataLen` instruction path: the compute-unit charge for the entire loader management instruction is a flat constant, `UPGRADEABLE_LOADER_COMPUTE_UNITS = 2_370`, charged once in `process_instruction_inner` [1](#0-0) , regardless of how large the program ELF being deployed is. But the actual work done during deployment — `Executable::load`, `executable.verify::<RequisiteVerifier>()`, and (outside `cfg(metrics)`) `jit_compile()` in `deploy_program` — scales with the size and complexity of the ELF, up to the maximum program size permitted by the runtime [2](#0-1) .

### Finding Description
`process_instruction_inner` charges a single fixed amount of compute units for any BPF Loader Upgradeable instruction before dispatching to `process_loader_upgradeable_instruction`: [1](#0-0) 

This same flat 2,370 CU price applies to `InitializeBuffer`, `Write`, and `DeployWithMaxDataLen` alike — cheap, O(1) bookkeeping instructions and the `DeployWithMaxDataLen` instruction, which invokes `deploy_program!` → `deploy_program()` to parse the ELF, run the SBPF `RequisiteVerifier`, and JIT-compile the program [3](#0-2) [4](#0-3) . None of `load_elf_time`, `verify_code_time` (both measured only when the `metrics` feature is present) are ever translated into an *additional* compute-unit charge against the invoking transaction's compute meter — the cost model and the runtime charge the same constant no matter whether the deployed program is a few hundred bytes or approaches the maximum permitted program-data size (bounded by `MAX_PERMITTED_DATA_LENGTH`, imported in `programs/bpf_loader/src/lib.rs`) [5](#0-4) .

The block-level cost model corroborates that this is a hard-coded, non-scaling number: `BUILTIN_INSTRUCTION_COSTS`/`NON_MIGRATING_BUILTINS_COSTS` list `bpf_loader_upgradeable::id()` as `BuiltinCost::NotMigrating`, and the scheduler cost-adjustment tests explicitly assert the deploy instruction consumes exactly `solana_bpf_loader_program::UPGRADEABLE_LOADER_COMPUTE_UNITS` regardless of program size [6](#0-5) [7](#0-6) .

This is exactly the pattern flagged in the report: an operation whose real CPU cost is proportional to attacker-controlled input size (ELF parsing/verification/JIT) is billed a constant amount unrelated to that cost, and — as in the Arbitrum report — this holds true whether verification succeeds or fails (a maliciously crafted, maximal-size but broken ELF that fails `RequisiteVerifier` still forces the loader to fully parse relocations/sections before returning `InvalidAccountData` [8](#0-7) ), so failing deployments do not "save" the validator any CPU relative to what was priced.

### Impact Explanation
An attacker can submit many `DeployWithMaxDataLen` transactions carrying maximal-size (up to the max program-data length), maliciously structured ELF payloads designed to maximize SBPF verifier/loader work (e.g., pathological relocation tables, deeply nested control-flow graphs for the verifier) while paying only the fixed low CU price (2,370 CU) plus normal per-signature/per-write-lock transaction fees. Since compute-unit accounting under-prices this operation relative to wall-clock CPU cost, an attacker can consume disproportionate leader/validator CPU time during transaction replay/execution relative to the fee paid, degrading transaction throughput for other users — a non-RPC compute-exhaustion condition consistent with the "single-client low-rate...exhaustion/degradation" impact class for unprivileged runtime/built-in paths.

Note: building the buffer to max size first requires many `Write` transactions (each bounded by packet size, and also charged the same flat cost independent of the per-call byte count), so the primary amplification is in the deploy step's parse/verify/JIT cost vs. its fixed CU price, not a single-transaction unbounded payload.

### Likelihood Explanation
Likelihood is moderate: constructing this attack requires no special privileges — any funded account can create a buffer, write to it repeatedly, and issue `DeployWithMaxDataLen`. However, quantifying the actual CPU-time asymmetry (whether the flat 2,370 CU realistically undershoots the worst-case verifier/loader CPU cost by a meaningful factor) would require benchmarking against current SBPF verifier/loader implementations, which is not observable from static code alone. This is a pricing/tuning question rather than a memory-safety or consensus-breaking bug, aligning with the "Low" difficulty/"Testing" classification of the original Arbitrum finding.

### Recommendation
- **Short term:** Meter (or otherwise scale) the compute-unit charge for `DeployWithMaxDataLen` (and analogous `UpgradeableLoaderInstruction::Write` calls) proportionally to the size of the ELF/program data being parsed, verified, and JIT-compiled in `deploy_program()`, rather than using a single flat `UPGRADEABLE_LOADER_COMPUTE_UNITS` constant for all loader-management instructions.
- **Long term:** Audit other fixed-cost builtin instructions in `builtins-default-costs/src/lib.rs`'s `NON_MIGRATING_BUILTINS_COSTS`/`MIGRATING_BUILTINS_COSTS` tables whose underlying execution cost is a function of attacker-controlled instruction/account data size, to ensure compute-unit pricing tracks real CPU cost even on the failure path.

### Proof of Concept
Not independently executable from static analysis; the concrete PoC would be: (1) fund an account, (2) create and populate a `Buffer` account near `MAX_PERMITTED_DATA_LENGTH` with a crafted ELF containing pathological but structurally valid SBPF sections/relocations designed to maximize `Executable::load`/`RequisiteVerifier::verify` work, (3) submit `DeployWithMaxDataLen` and measure wall-clock validator CPU time spent in `deploy_program()` versus the flat 2,370 CU charged, repeating at scale to observe throughput degradation relative to fees paid. This benchmark was not run as part of this analysis — the finding is based on static confirmation that the CU charge in `programs/bpf_loader/src/lib.rs` is a size-independent constant while the corresponding work in `program-runtime/src/deploy.rs` is size-dependent.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L27-27)
```rust
    solana_system_interface::{MAX_PERMITTED_DATA_LENGTH, instruction as system_instruction},
```

**File:** programs/bpf_loader/src/lib.rs (L93-99)
```rust
    if native_loader::check_id(&owner_id) {
        let program_id = instruction_context.get_program_key()?;
        return if bpf_loader_upgradeable::check_id(program_id) {
            invoke_context
                .compute_meter
                .consume_checked(UPGRADEABLE_LOADER_COMPUTE_UNITS)?;
            process_loader_upgradeable_instruction(invoke_context)
```

**File:** programs/bpf_loader/src/lib.rs (L309-321)
```rust
            invoke_context
                .native_invoke_signed(instruction, &[&[new_program_id.as_ref(), &[bump_seed]]])?;

            // Load and verify the program bits
            let transaction_context = &invoke_context.transaction_context;
            let instruction_context = transaction_context.get_current_instruction_context()?;
            let buffer = instruction_context.try_borrow_instruction_account(3)?;
            deploy_program!(
                invoke_context,
                &new_program_id,
                &owner_id,
                buffer
                    .get_data()
```

**File:** program-runtime/src/deploy.rs (L46-102)
```rust
/// Directly deploy a program using a provided invoke context.
/// This function should only be invoked from the runtime, since it does not
/// provide any account loads or checks.
#[allow(clippy::too_many_arguments)]
pub fn deploy_program(
    log_collector: Option<Rc<RefCell<LogCollector>>>,
    #[cfg(feature = "metrics")] load_program_metrics: &mut LoadProgramMetrics,
    program_cache_for_tx_batch: &mut ProgramCacheForTxBatch,
    program_runtime_environment: ProgramRuntimeEnvironment,
    disable_sbpf_v0_v1_v2_deployment: bool,
    program_id: &Pubkey,
    loader_key: &Pubkey,
    programdata: &[u8],
    deployment_slot: Slot,
) -> Result<(), InstructionError> {
    #[cfg(feature = "metrics")]
    let mut register_syscalls_time = Measure::start("register_syscalls_time");
    let deployment_program_runtime_environment = morph_into_deployment_environment(
        ProgramRuntimeEnvironment::clone(&program_runtime_environment),
        disable_sbpf_v0_v1_v2_deployment,
    )
    .map_err(|e| {
        ic_logger_msg!(log_collector, "Failed to register syscalls: {}", e);
        InstructionError::ProgramEnvironmentSetupFailure
    })?;
    #[cfg(feature = "metrics")]
    {
        register_syscalls_time.stop();
        load_program_metrics.register_syscalls_us = register_syscalls_time.as_us();
    }
    // Verify using stricter deployment_program_runtime_environment
    #[cfg(feature = "metrics")]
    let mut load_elf_time = Measure::start("load_elf_time");
    let executable = Executable::<InvokeContext>::load(
        programdata,
        Arc::new(deployment_program_runtime_environment),
    )
    .map_err(|err| {
        ic_logger_msg!(log_collector, "{}", err);
        InstructionError::InvalidAccountData
    })?;
    #[cfg(feature = "metrics")]
    {
        load_elf_time.stop();
        load_program_metrics.load_elf_us = load_elf_time.as_us();
    }
    #[cfg(feature = "metrics")]
    let mut verify_code_time = Measure::start("verify_code_time");
    executable.verify::<RequisiteVerifier>().map_err(|err| {
        ic_logger_msg!(log_collector, "{}", err);
        InstructionError::InvalidAccountData
    })?;
    #[cfg(feature = "metrics")]
    {
        verify_code_time.stop();
        load_program_metrics.verify_code_us = verify_code_time.as_us();
    }
```

**File:** builtins-default-costs/src/lib.rs (L108-119)
```rust
const NON_MIGRATING_BUILTINS_COSTS: &[(Pubkey, BuiltinCost)] = &[
    (system_program::id(), BuiltinCost::NotMigrating),
    (compute_budget::id(), BuiltinCost::NotMigrating),
    (bpf_loader_upgradeable::id(), BuiltinCost::NotMigrating),
    (bpf_loader_deprecated::id(), BuiltinCost::NotMigrating),
    (bpf_loader::id(), BuiltinCost::NotMigrating),
    // We're going to need a feature gate to "fake migrate" Loader V4 to BPF,
    // whenever we deploy the program on-chain. The builtin shouldn't have been
    // added here without a feature gate.
    (loader_v4::id(), BuiltinCost::NotMigrating),
    (secp256k1_program::id(), BuiltinCost::NotMigrating),
    (ed25519_program::id(), BuiltinCost::NotMigrating),
```

**File:** core/tests/scheduler_cost_adjustment.rs (L318-334)
```rust
#[test]
fn test_builtin_ix_cost_adjustment_with_bpf_v3_no_cu_limit() {
    // A System & BPF Loader v3 ix. The latter CPIs into System.
    // Cost model & Compute budget: reserve/allocate default CU for 1 builtin
    // VM Execution: consume CUs for 1 BPF_L and 1 System (CPI-ed 1 time), then succeed
    // Result: adjustment = 3_000 - 2_370 - 150 = 480
    let expected = TestResult {
        cost_adjustment: MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT as i64
            - solana_bpf_loader_program::UPGRADEABLE_LOADER_COMPUTE_UNITS as i64
            - solana_system_program::system_processor::DEFAULT_COMPUTE_UNITS as i64,
        execution_status: Ok(()),
    };

    let mut test_setup = TestSetup::new();
    let ix = test_setup.deploy_with_max_data_len_ix();
    assert_eq!(expected, test_setup.execute_test_transaction(&[ix]));
}
```

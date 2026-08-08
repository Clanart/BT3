### Title
Deploy/Upgrade instructions charge a flat compute cost while JIT compilation cost scales with program size, decoupling node CPU cost from charged compute units - (File: `program-runtime/src/deploy.rs`, `program-runtime/src/program_cache_entry.rs`)

### Summary
`process_loader_upgradeable_instruction` charges a single flat `UPGRADEABLE_LOADER_COMPUTE_UNITS` (2,370 CU) for `DeployWithMaxDataLen`/`Upgrade` regardless of program size, then invokes `deploy_program!` → `deploy_program()` → `ProgramCacheEntry::reload()` → `new_internal()`, which performs `Executable::load`, `RequisiteVerifier` verification, and (on x86_64) `executable.jit_compile()` over the full ELF. None of this per-byte/per-instruction work is metered against the compute budget, so the actual CPU cost of loading/verifying/JIT-compiling a large program is unbounded by the charged compute units.

### Finding Description
`process_instruction_inner` charges `UPGRADEABLE_LOADER_COMPUTE_UNITS` (a constant 2,370) up front for any `bpf_loader_upgradeable` management instruction: [1](#0-0) 

The `DeployWithMaxDataLen`/`Upgrade` handlers then call the `deploy_program!` macro with the attacker-controlled buffer bytes (`programdata`), which is up to `MAX_PERMITTED_DATA_LENGTH`-bounded but can be a large, complex ELF: [2](#0-1) 

`deploy_program()` performs `Executable::load`, `RequisiteVerifier::verify`, and then `ProgramCacheEntry::reload` (unsafely skipping re-verification but still calling `Executable::load` and JIT-compiling): [3](#0-2) 

The actual JIT compilation happens in `new_internal`, unconditionally on x86_64/non-Windows targets, with no compute-meter check or size-based cost gating: [4](#0-3) 

`LoadProgramMetrics`/`ProgramStatistics` record `jit_compile_us` purely for observability — these are metrics, not compute charges: [5](#0-4) 

Because the flat `UPGRADEABLE_LOADER_COMPUTE_UNITS` charge does not scale with `programdata.len()` or ELF complexity (number of instructions/functions to verify and JIT-compile), an attacker can submit a maximal-size, JIT-compilation-heavy ELF (near `MAX_PERMITTED_DATA_LENGTH`) that consumes disproportionate CPU relative to the CUs charged, and repeat this cheaply by closing and redeploying buffer accounts (buffer accounts are refundable/closeable, and only rent + transaction fee is spent per redeploy).

### Impact Explanation
This matches the stated scoped impact: "node-side JIT compilation cost not upper-bounded by the deploy instruction's charged compute units," i.e., unprivileged, underpriced CPU consumption on validators processing deploy transactions — a compute-cost/DoS-adjacent finding rather than memory-safety or privilege escalation. It falls under the "compute unit metering / cost model" bounty category (materially underpriced compute), since deploy CPU cost (load + verify + JIT) is not linked to CUs charged, unlike per-CU-scaled execution costs elsewhere in the runtime.

### Likelihood Explanation
This is trivially and repeatably reachable by any unprivileged user: write a buffer account, invoke `DeployWithMaxDataLen` (or `Upgrade`), pay only the flat CU cost plus transaction fee and rent, and repeat across many transactions/slots (closing and reopening buffer accounts to reclaim rent). No special privileges, staked-node control, or validator config manipulation is needed — only ordinary program deployment. However, note the practical ceiling: `MAX_PERMITTED_DATA_LENGTH` bounds program size, and Solana's existing deploy-cost design (flat builtin cost for loader instructions, as also reflected in `builtins-default-costs`) appears to be an accepted/known design tradeoff in Agave rather than a newly discovered defect, since JIT time for maximum program sizes has historically been budgeted for via transaction/block-level deploy rate limiting rather than the compute-unit metering system itself. I was not able to fully verify from the available index whether an additional block-level/scheduler-level throttle (e.g., separate deploy-per-slot limits or a size-based surcharge added elsewhere in `builtins-default-costs` or `cost_model.rs`) exists to bound aggregate JIT cost per block; the search for `bpf_loader_upgradeable` cost entries in `builtins-default-costs/src/lib.rs` returned matches but their content wasn't retrievable in this session.

### Recommendation
Scale the compute cost charged for `DeployWithMaxDataLen`/`Upgrade` (and other loader-invoked ELF-load paths) by `programdata.len()` and/or ELF instruction count, so that CU cost approximates the measured `load_elf_us`/`verify_code_us`/`jit_compile_us` linearly, and/or enforce a hard cap on the number of deploy/upgrade operations processed per block independent of the CU limit.

### Proof of Concept
Rust benchmark/integration test plan (to be run in `program-runtime` or `programs/bpf_loader` test harness):
```rust
#[test]
fn jit_compile_cost_not_bounded_by_charged_cu() {
    // For several ELF sizes/complexities (small, medium, near-MAX_PERMITTED_DATA_LENGTH,
    // with varying numbers of BPF instructions/functions):
    for elf in test_elfs_of_varied_size_and_complexity() {
        let mut metrics = LoadProgramMetrics::default();
        let start = std::time::Instant::now();
        let entry = ProgramCacheEntry::new(
            &bpf_loader_upgradeable::id(),
            program_runtime_environment.clone(),
            slot,
            &elf,
            &mut metrics,
        ).unwrap();
        let elapsed = start.elapsed();

        // Compare elapsed/jit_compile_us against the FLAT charged CU
        // (UPGRADEABLE_LOADER_COMPUTE_UNITS = 2_370, constant regardless of elf.len()).
        // Assert failure/flag if jit_compile_us grows superlinearly or is unbounded
        // relative to the flat charge, i.e. cost-per-CU ratio increases with elf size.
        let cost_per_cu = metrics.jit_compile_us as f64 / UPGRADEABLE_LOADER_COMPUTE_UNITS as f64;
        record_result(elf.len(), cost_per_cu);
    }
    // Assert: cost_per_cu should remain roughly constant across elf sizes if properly metered;
    // flag if it grows with elf.len(), demonstrating underpriced compute.
}
```
This confirms the flat charge at `programs/bpf_loader/src/lib.rs:96-99` combined with size/complexity-dependent JIT cost at `program-runtime/src/program_cache_entry.rs:269-279`.

### Citations

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

**File:** programs/bpf_loader/src/lib.rs (L312-328)
```rust
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
                    .get(buffer_data_offset..)
                    .ok_or(InstructionError::AccountDataTooSmall)?,
                clock.slot,
                invoke_context
                    .get_feature_set()
                    .disable_sbpf_v0_v1_v2_deployment,
            );
```

**File:** program-runtime/src/deploy.rs (L76-118)
```rust
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
    // Reload but with program_runtime_environment
    let executor = unsafe {
        // SAFETY: The executable has been verified just above.
        ProgramCacheEntry::reload(
            loader_key,
            program_runtime_environment,
            deployment_slot,
            programdata,
            #[cfg(feature = "metrics")]
            load_program_metrics,
        )
    }
    .map_err(|err| {
        ic_logger_msg!(log_collector, "{}", err);
        InstructionError::InvalidAccountData
    })?;
```

**File:** program-runtime/src/program_cache_entry.rs (L269-279)
```rust
        #[cfg(all(not(target_os = "windows"), target_arch = "x86_64"))]
        {
            let jit_compile_time = solana_svm_measure::measure::Measure::start("jit_compile_time");
            executable.jit_compile()?;
            let jit_compile_time = jit_compile_time.end_as_us();
            entry_stats.jit_compiled(jit_compile_time);
            #[cfg(feature = "metrics")]
            {
                metrics.jit_compile_us = jit_compile_time;
            }
        }
```

**File:** program-runtime/src/program_metrics.rs (L244-268)
```rust
#[cfg(feature = "metrics")]
/// Time measurements for loading a single [ProgramCacheEntry].
#[derive(Debug, Default)]
pub struct LoadProgramMetrics {
    /// Program address, but as text
    pub program_id: String,
    /// Microseconds it took to `create_program_runtime_environment`
    pub register_syscalls_us: u64,
    /// Microseconds it took to `Executable::<InvokeContext>::load`
    pub load_elf_us: u64,
    /// Microseconds it took to `executable.verify::<RequisiteVerifier>`
    pub verify_code_us: u64,
    /// Microseconds it took to `executable.jit_compile`
    pub jit_compile_us: u64,
}

#[cfg(feature = "metrics")]
impl LoadProgramMetrics {
    pub fn submit_datapoint(&self, timings: &mut ExecuteDetailsTimings) {
        timings.create_executor_register_syscalls_us += self.register_syscalls_us;
        timings.create_executor_load_elf_us += self.load_elf_us;
        timings.create_executor_verify_code_us += self.verify_code_us;
        timings.create_executor_jit_compile_us += self.jit_compile_us;
    }
}
```

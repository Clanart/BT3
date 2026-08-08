### Title
CPI `translate_signers` performs unbounded SHA-256 PDA derivation hashing with no compute charge tied to seed count or seed byte length - (File: program-runtime/src/cpi.rs)

### Summary
`translate_signers` in `program-runtime/src/cpi.rs` iterates over up to `MAX_SIGNERS` (16) signer-seed groups, each with up to `MAX_SEEDS` seeds, and calls `Pubkey::create_program_address` (SHA-256-based hashing) for every group, but neither this function nor its caller `cpi_common` charges any compute units proportional to the number of signers or the total seed bytes hashed. [1](#0-0)  The only CU charge in `cpi_common` prior to this call is the flat `invoke_units` constant, unrelated to seed volume. [2](#0-1) 

### Finding Description
`cpi_common` is the shared entry point for both Rust- and C-ABI `invoke_signed` CPI syscalls. It consumes a flat `invoke_units` compute charge, then calls `translate_signers(caller_program_id, signers_seeds_addr, signers_seeds_len, invoke_context)` before any per-seed cost accounting occurs. [3](#0-2) 

Inside `translate_signers`, the code:
1. Translates the outer `signers_seeds` slice and rejects it only if `signers_seeds.len() > MAX_SIGNERS` (16). [4](#0-3) 
2. For each of up to 16 signer entries, translates an inner seed slice and rejects only if `untranslated_seeds.len() > MAX_SEEDS`. [5](#0-4) 
3. Materializes each seed's bytes and calls `Pubkey::create_program_address(&seeds_bytes, program_id)`, which performs SHA-256 hashing over the concatenation of all seed bytes, the program id, and the PDA marker. [6](#0-5) 

No `invoke_context.compute_meter.consume_checked(...)` call exists anywhere in `translate_signers` for the hashing work it performs — unlike `translate_instruction_rust`/`translate_instruction_c` and `translate_account_infos`, which explicitly charge CUs proportional to `data.len()` and `account_infos_bytes` respectively via `cpi_bytes_per_unit`. [7](#0-6) [8](#0-7)  The `create_program_address_units` cost constant referenced elsewhere (`compute-budget/src/compute_budget.rs`, `syscalls/src/lib.rs`, `program-runtime/src/execution_budget.rs`) is applied to the standalone `sol_create_program_address`/`sol_try_find_program_address` syscalls, not to the CPI signer-derivation path in `cpi.rs`. Consequently, an attacker invoking CPI with `signers_seeds_len` at its cap (16 signer groups), each with `MAX_SEEDS` seeds near the maximum seed length, forces up to 16 separate SHA-256-based `create_program_address` computations (each hashing on the order of hundreds of bytes) while `cpi_common` only ever deducts the single flat `invoke_units` charge for the entire CPI call — a cost that does not scale with signer count or seed bytes at all.

### Impact Explanation
This is a materially underpriced compute finding: the CPU cost of `Pubkey::create_program_address`-based PDA derivation (SHA-256 hashing of up to 16 signer groups × up to 16 seeds × up to `MAX_SEED_LEN` bytes each) inside `translate_signers` is not charged proportionally — or even charged with a flat per-signer amount — against the compute meter in the CPI path. Repeating maximal-signer CPI calls within a transaction's compute budget lets an attacker perform disproportionately more SHA-256 hashing work per charged CU than the compute-unit accounting model assumes, falling under Agave's "materially underpriced compute" bounty category.

### Likelihood Explanation
Fully reachable by an unprivileged attacker: any deployed sBPF program can invoke CPI (`invoke_signed`) with `signers_seeds_len` set to `MAX_SIGNERS` and seed groups populated up to `MAX_SEEDS` with near-maximum-length seed bytes, with no special privileges required. The path `cpi_common -> translate_signers` is on every signed CPI call, is fully attacker-controlled via instruction data laid out in VM memory, and is trivially repeatable across many CPI invocations within a single transaction/compute budget.

### Recommendation
Add explicit compute-unit accounting in `translate_signers` (or in `cpi_common` immediately around the call) that charges CUs proportional to the total seed bytes processed and/or the number of signer groups — mirroring the existing `create_program_address_units`/hashing cost model used for the standalone `sol_create_program_address` syscall — before or while performing the `Pubkey::create_program_address` hashing for each signer group.

### Proof of Concept
Rust unit test plan for `program-runtime/src/cpi.rs`:
```rust
#[test]
fn test_translate_signers_cu_charge_scales_with_seed_bytes() {
    // Construct a mock InvokeContext with a compute meter set to a fixed budget.
    // Case A: signers_seeds with 1 signer group, 1 seed of length 1.
    // Case B: signers_seeds with MAX_SIGNERS (16) groups, each with MAX_SEEDS seeds
    //         of length MAX_SEED_LEN (32) bytes (attacker-supplied maximal layout).
    //
    // For both cases, record compute_meter.get_remaining() before and after
    // calling translate_signers(...).
    //
    // Expected (bug) result: consumed CU delta is identical (or near-identical)
    // between Case A and Case B, despite Case B hashing ~256x more seed bytes
    // via Pubkey::create_program_address.
    //
    // Assertion that should hold after fix but currently fails:
    // assert!(cu_consumed_case_b > cu_consumed_case_a * some_scaling_factor);
}
```
A benchmark (`criterion`) comparing wall-clock time of `translate_signers` for minimal vs. maximal seed volume would further show CPU time scaling with seed bytes while CU consumption (as currently implemented) stays flat/zero for this specific code path.

### Citations

**File:** program-runtime/src/cpi.rs (L560-575)
```rust
    let mut total_cu_translation_cost: u64 = (data.len() as u64)
        .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
        .unwrap_or(u64::MAX);

    // Each account meta is 34 bytes (32 for pubkey, 1 for is_signer, 1 for is_writable)
    let account_meta_translation_cost =
        (account_metas.len().saturating_mul(size_of::<AccountMeta>()) as u64)
            .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
            .unwrap_or(u64::MAX);

    total_cu_translation_cost =
        total_cu_translation_cost.saturating_add(account_meta_translation_cost);

    invoke_context
        .compute_meter
        .consume_checked(total_cu_translation_cost)?;
```

**File:** program-runtime/src/cpi.rs (L631-674)
```rust
pub fn translate_signers(
    program_id: &Pubkey,
    signers_seeds_addr: u64,
    signers_seeds_len: u64,
    invoke_context: &InvokeContext,
) -> Result<Vec<Pubkey>, Error> {
    let check_aligned = invoke_context.get_check_aligned();
    let memory_mapping = invoke_context.memory_contexts.memory_mapping()?;
    if signers_seeds_len > 0 {
        let signers_seeds = translate_slice::<VmSlice<VmSlice<u8>>>(
            memory_mapping,
            signers_seeds_addr,
            signers_seeds_len,
            check_aligned,
        )?;
        if signers_seeds.len() > MAX_SIGNERS {
            return Err(Box::new(CpiError::TooManySigners));
        }
        Ok(signers_seeds
            .iter()
            .map(|signer_seeds| {
                let untranslated_seeds = translate_slice::<VmSlice<u8>>(
                    memory_mapping,
                    signer_seeds.ptr(),
                    signer_seeds.len(),
                    check_aligned,
                )?;
                if untranslated_seeds.len() > MAX_SEEDS {
                    return Err(Box::new(InstructionError::MaxSeedLengthExceeded) as Error);
                }
                let seeds_bytes = untranslated_seeds
                    .iter()
                    .map(|untranslated_seed| {
                        translate_vm_slice(untranslated_seed, memory_mapping, check_aligned)
                    })
                    .collect::<Result<Vec<_>, Error>>()?;
                Pubkey::create_program_address(&seeds_bytes, program_id)
                    .map_err(|err| Box::new(CpiError::BadSeeds(err)) as Error)
            })
            .collect::<Result<Vec<_>, Error>>()?)
    } else {
        Ok(vec![])
    }
}
```

**File:** program-runtime/src/cpi.rs (L785-806)
```rust
    let amount = invoke_context.get_execution_cost().invoke_units;
    invoke_context.compute_meter.consume_checked(amount)?;
    let syscall_parameter_address_restrictions = invoke_context
        .get_feature_set()
        .syscall_parameter_address_restrictions;
    let virtual_address_space_adjustments = invoke_context
        .get_feature_set()
        .virtual_address_space_adjustments;
    let account_data_direct_mapping = invoke_context.get_feature_set().account_data_direct_mapping;
    let check_aligned = invoke_context.get_check_aligned();

    let instruction = S::translate_instruction(instruction_addr, invoke_context)?;
    let instruction_context = invoke_context
        .transaction_context
        .get_current_instruction_context()?;
    let caller_program_id = instruction_context.get_program_key()?;
    let signers = translate_signers(
        caller_program_id,
        signers_seeds_addr,
        signers_seeds_len,
        invoke_context,
    )?;
```

**File:** program-runtime/src/cpi.rs (L933-938)
```rust
    let account_infos_bytes = account_infos.len().saturating_mul(ACCOUNT_INFO_BYTE_SIZE);

    let amount = (account_infos_bytes as u64)
        .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
        .unwrap_or(u64::MAX);
    invoke_context.compute_meter.consume_checked(amount)?;
```

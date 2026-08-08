### Title
Unmetered SHA256 PDA-derivation work in CPI signer-seed processing - ([File: program-runtime/src/cpi.rs])

### Summary
`translate_signers` in `program-runtime/src/cpi.rs` calls `Pubkey::create_program_address` (SHA256-based) once per signer-seed set supplied to `invoke_signed`, but never charges the dedicated `create_program_address_units` compute cost that the equivalent direct syscalls (`SyscallCreateProgramAddress`, `SyscallTryFindProgramAddress`) charge for the same operation. The only compute charged for the whole CPI is the flat `invoke_units` fee applied once in `cpi_common`, so the SHA256 work scales with attacker-controlled seed volume while the price does not.

### Finding Description
`cpi_common` (program-runtime/src/cpi.rs:773-786) charges a single flat fee via `invoke_context.compute_meter.consume_checked(invoke_context.get_execution_cost().invoke_units)` before doing any signer processing. It then calls `translate_signers` (program-runtime/src/cpi.rs:631-674), which, for each of up to `MAX_SIGNERS` signer-seed sets and up to `MAX_SEEDS` seeds per set, translates the seed bytes and calls `Pubkey::create_program_address(&seeds_bytes, program_id)` directly — with no `compute_meter.consume_checked` call anywhere in the function body [1](#0-0) .

Contrast this with the standalone `sol_create_program_address` / `sol_try_find_program_address` syscalls, which explicitly charge `create_program_address_units` before doing the identical `Pubkey::create_program_address` work, and even re-charge the cost on every bump-seed iteration inside `SyscallTryFindProgramAddress`'s retry loop [2](#0-1) [3](#0-2) . The `create_program_address_units` field exists specifically to price this SHA256-based derivation [4](#0-3) , and a codebase-wide search confirms it is only ever consumed in `syscalls/src/lib.rs`, never in `program-runtime/src/cpi.rs`.

An unprivileged attacker who deploys their own sBPF program can invoke `invoke_signed`/`sol_invoke_signed` with `signers_seeds_addr`/`signers_seeds_len` pointing at `MAX_SIGNERS` signer-seed sets, each containing `MAX_SEEDS` seeds of near-maximum length. `translate_signers` will perform `MAX_SIGNERS` calls to `Pubkey::create_program_address`, each hashing up to `MAX_SEEDS` seed slices via SHA256, all for the price of one flat `invoke_units` charge that is otherwise identical regardless of whether zero or the maximum number of signer seeds are supplied.

### Impact Explanation
This is a materially underpriced compute path: the cost of a CPI's signer-seed derivation is not proportional to the actual SHA256 work performed, unlike the compute-metering invariant enforced everywhere else in the syscall surface (e.g., `SyscallCreateProgramAddress`, `SyscallLogData`'s per-byte charging, `translate_account_infos`'s per-byte charging). This falls under the Agave "underpriced compute / CU accounting" bounty category — it lets a program get SHA256-based PDA derivation work essentially for free during CPI, independent of seed volume, which could be used to pack more real SHA256 work per compute unit budget than the fee schedule intends.

### Likelihood Explanation
Fully attacker-controlled and requires no privileged access: the attacker only needs to deploy their own BPF program and issue a single CPI syscall (`invoke_signed`) with maximal seed sets and lengths. This is 100% reproducible on every invocation and does not depend on validator/leader state, timing, or race conditions.

### Recommendation
Add an explicit `compute_meter.consume_checked` call inside `translate_signers` (or in `cpi_common` immediately before/after calling it) that charges `create_program_address_units` (or an equivalent per-seed-set cost) for each `Pubkey::create_program_address` call performed, mirroring the charging done in `SyscallCreateProgramAddress`/`SyscallTryFindProgramAddress`, so the cost scales with `MAX_SIGNERS` × seed count/size actually supplied.

### Proof of Concept
Rust unit test plan (to be added alongside `test_translate_signers` in `program-runtime/src/cpi.rs`):

```rust
#[test]
fn test_translate_signers_unmetered_pda_derivation() {
    // Set up mock invoke_context with a fixed, generous compute budget
    // (e.g., mock_set_remaining(1_000_000)).

    // Case A: zero signer seeds — record compute_meter remaining before/after `translate_signers`.
    let remaining_before_a = invoke_context.compute_meter.remaining(); // via test helper
    translate_signers(&program_id, 0, 0, &invoke_context).unwrap();
    let consumed_a = remaining_before_a - invoke_context.compute_meter.remaining();

    // Case B: MAX_SIGNERS signer-seed sets, each with MAX_SEEDS seeds of MAX_SEED_LEN bytes,
    // laid out in mock VM memory (similar to `mock_signers` helper).
    let remaining_before_b = invoke_context.compute_meter.remaining();
    translate_signers(&program_id, vm_addr, MAX_SIGNERS as u64, &invoke_context).unwrap();
    let consumed_b = remaining_before_b - invoke_context.compute_meter.remaining();

    // Assertion exposing the bug: compute consumed by translate_signers itself is 0 in both
    // cases (all charging happens elsewhere, flatly, in cpi_common), i.e. consumed_a == consumed_b == 0,
    // even though case B performs MAX_SIGNERS SHA256-based create_program_address calls
    // over MAX_SEEDS * MAX_SEED_LEN bytes each.
    assert_eq!(consumed_a, 0);
    assert_eq!(consumed_b, 0); // fails to scale with seed volume -> confirms underpricing
}
```

A complementary integration-level PoC would use the existing `programs/sbf/rust/invoke` test harness (which already has `TEST_CU_USAGE_*` cases measuring CU cost via `sol_remaining_compute_units()` around `invoke_signed` calls, e.g. lines 1559-1648) to add a new case that calls `invoke_signed` with `MAX_SIGNERS` maximal signer-seed sets vs. zero signer seeds, and assert that the CU delta between the two calls is (near) zero — demonstrating that PDA-derivation cost is not billed proportionally to seed volume, only the flat `invoke_units` cost is charged.

### Citations

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

**File:** syscalls/src/lib.rs (L821-858)
```rust
declare_builtin_function!(
    /// Create a program address
    SyscallCreateProgramAddress,
    fn rust(
        invoke_context: &mut InvokeContext<'_, '_>,
        seeds_addr: u64,
        seeds_len: u64,
        program_id_addr: u64,
        address_addr: u64,
        _arg5: u64,
    ) -> Result<u64, Error> {
        let cost = invoke_context
            .get_execution_cost()
            .create_program_address_units;
        invoke_context.compute_meter.consume_checked(cost)?;

        let check_aligned = invoke_context.get_check_aligned();
        let memory_mapping = invoke_context.memory_contexts.memory_mapping_mut()?;
        let (seeds, program_id) = translate_and_check_program_address_inputs(
            seeds_addr,
            seeds_len,
            program_id_addr,
            memory_mapping,
            check_aligned,
        )?;

        let Ok(new_address) = Pubkey::create_program_address(&seeds, program_id) else {
            return Ok(1);
        };
        translate_mut!(
            memory_mapping,
            check_aligned,
            let address: (&mut [MaybeUninit<u8>]) = map(address_addr, std::mem::size_of::<Pubkey>() as u64)?;
        );
        address.write_copy_of_slice(new_address.as_ref());
        Ok(0)
    }
);
```

**File:** syscalls/src/lib.rs (L860-911)
```rust
declare_builtin_function!(
    /// Create a program address
    SyscallTryFindProgramAddress,
    fn rust(
        invoke_context: &mut InvokeContext<'_, '_>,
        seeds_addr: u64,
        seeds_len: u64,
        program_id_addr: u64,
        address_addr: u64,
        bump_seed_addr: u64,
    ) -> Result<u64, Error> {
        let cost = invoke_context
            .get_execution_cost()
            .create_program_address_units;
        invoke_context.compute_meter.consume_checked(cost)?;

        let check_aligned = invoke_context.get_check_aligned();
        let memory_mapping = invoke_context.memory_contexts.memory_mapping_mut()?;
        let (seeds, program_id) = translate_and_check_program_address_inputs(
            seeds_addr,
            seeds_len,
            program_id_addr,
            memory_mapping,
            check_aligned,
        )?;

        let mut bump_seed = [u8::MAX];
        for _ in 0..u8::MAX {
            {
                let mut seeds_with_bump = seeds.to_vec();
                seeds_with_bump.push(&bump_seed);

                if let Ok(new_address) =
                    Pubkey::create_program_address(&seeds_with_bump, program_id)
                {
                    translate_mut!(
                        memory_mapping,
                        check_aligned,
                        let bump_seed_ref: (&mut MaybeUninit<u8>) = map(bump_seed_addr)?;
                        let address: (&mut [MaybeUninit<u8>]) = map(address_addr, std::mem::size_of::<Pubkey>() as u64)?;
                    );
                    bump_seed_ref.write(bump_seed[0]);
                    address.write_copy_of_slice(new_address.as_ref());
                    return Ok(0);
                }
            }
            bump_seed[0] = bump_seed[0].saturating_sub(1);
            invoke_context.compute_meter.consume_checked(cost)?;
        }
        Ok(1)
    }
);
```

**File:** compute-budget/src/compute_budget.rs (L21-22)
```rust
    /// Number of compute units consumed by a create_program_address call
    pub create_program_address_units: u64,
```

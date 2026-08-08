### Title
CPI signer verification in `translate_signers` performs uncharged `create_program_address` (SHA256) hashing, unlike the direct syscall - (File: program-runtime/src/cpi.rs)

### Summary
`cpi_common` charges a single flat `invoke_units` fee (946 CU by default) per CPI call and then calls `translate_signers`, which performs up to `MAX_SIGNERS` `Pubkey::create_program_address` hash computations (one per signer seed set, each with up to `MAX_SEEDS` seeds of up to `MAX_SEED_LEN` bytes) with zero additional compute charge. This is inconsistent with the same primitive exposed directly as a syscall (`sol_create_program_address`/`sol_try_find_program_address`), where each single `create_program_address` computation is explicitly billed `create_program_address_units` (1500 CU).

### Finding Description
`cpi_common` (`program-runtime/src/cpi.rs:773-808`) consumes the flat `invoke_units` cost once at the top of the function: [1](#0-0) 
It then calls `translate_signers(caller_program_id, signers_seeds_addr, signers_seeds_len, invoke_context)` before `check_authorized_program`: [2](#0-1) 

`translate_signers` itself accepts up to `MAX_SIGNERS` seed sets, each with up to `MAX_SEEDS` seeds, and for every seed set calls `Pubkey::create_program_address(&seeds_bytes, program_id)` — a real SHA256-based hashing operation — with no compute-meter interaction at all: [3](#0-2) 

Contrast this with the standalone syscalls that expose the exact same primitive to a program directly. `SyscallCreateProgramAddress` and `SyscallTryFindProgramAddress` both explicitly charge `create_program_address_units` (1500 CU by default) per invocation before doing the identical `Pubkey::create_program_address` call: [4](#0-3) [5](#0-4) 

So the same underlying operation (`Pubkey::create_program_address`, which does SHA256 hashing over seed bytes) is billed 1500 CU when invoked directly via `sol_create_program_address`, but is essentially free (bundled into the single flat `invoke_units` charge regardless of how many signer sets are processed) when invoked implicitly through `invoke_signed`'s `signers_seeds` argument. An attacker-controlled program can populate `signers_seeds` with the maximum number of signer entries (`MAX_SIGNERS`), each containing the maximum number of seeds (`MAX_SEEDS`) at the maximum seed length (`MAX_SEED_LEN`), forcing up to `MAX_SIGNERS` real PDA derivations per CPI call for a flat, size-independent cost. This can be repeated across `max_instruction_trace_length` CPIs in a single transaction, multiplying the effect. No existing guard charges compute proportional to the number of signer seed sets or their total byte length in this path — the only checks present (`signers_seeds.len() > MAX_SIGNERS` and `untranslated_seeds.len() > MAX_SEEDS`) are correctness bounds, not compute-cost enforcement.

### Impact Explanation
This is a real, materially underpriced compute path: a program can force the runtime to perform CPU work (multiple SHA256-based PDA derivations) whose real cost is not reflected in the compute units charged. Since compute unit accounting is the mechanism used to bound per-transaction/per-block execution cost and fees, an attacker can craft transactions that consume disproportionately more validator CPU time than their charged compute units indicate, degrading node performance relative to the fee/CU paid — matching the "materially underpriced compute" bounty category referenced in the question's scope.

### Likelihood Explanation
This requires only an unprivileged attacker who deploys an arbitrary sBPF program invoking `invoke_signed`/`sol_invoke_signed_c` with a maximal `signers_seeds` array. No special privileges, staked node control, or leader control are needed — a normal deployed program executing a single CPI (or many, up to `max_instruction_trace_length`) can trigger this every time it runs. It is fully deterministic and reproducible.

### Recommendation
Charge compute units in `translate_signers` proportional to the number of signer seed sets and the total seed bytes processed (e.g., reuse `create_program_address_units` per signer entry, or a cost model consistent with `SyscallCreateProgramAddress`), consuming from `invoke_context.compute_meter` before/while performing each `Pubkey::create_program_address` call inside `translate_signers`, rather than relying solely on the flat `invoke_units` CPI charge.

### Proof of Concept
Rust unit/integration test plan (in `program-runtime/src/cpi.rs` test module, alongside `test_translate_signers`):
1. Build a `signers_seeds` VM memory layout with `MAX_SIGNERS` entries, each containing `MAX_SEEDS` seeds of `MAX_SEED_LEN` bytes (mirroring `mock_signers` helper used in `test_translate_signers`, but at maxima).
2. Record `invoke_context.compute_meter` remaining units before and after calling `translate_signers` with this maximal input, and compare against the CU cost of performing an equivalent number of `Pubkey::create_program_address` calls via `SyscallCreateProgramAddress` (i.e., `MAX_SIGNERS * create_program_address_units`).
3. Assert that `translate_signers` consumes `0` compute units (or far less than `MAX_SIGNERS * create_program_address_units`), demonstrating the cost-model discrepancy.
4. As a differential/benchmark test, measure wall-clock CPU time of `translate_signers` at maxima vs. the flat `invoke_units` charged in `cpi_common`, and assert the ratio of (CPU time)/(CU charged) significantly exceeds the ratio observed for the direct `sol_create_program_address` syscall path, proving disproportionate underpricing.

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

**File:** program-runtime/src/cpi.rs (L781-786)
```rust
    // CPI entry.
    //
    // Translate the inputs to the syscall and synchronize the caller's account
    // changes so the callee can see them.
    let amount = invoke_context.get_execution_cost().invoke_units;
    invoke_context.compute_meter.consume_checked(amount)?;
```

**File:** program-runtime/src/cpi.rs (L800-807)
```rust
    let caller_program_id = instruction_context.get_program_key()?;
    let signers = translate_signers(
        caller_program_id,
        signers_seeds_addr,
        signers_seeds_len,
        invoke_context,
    )?;
    check_authorized_program(&instruction.program_id, &instruction.data, invoke_context)?;
```

**File:** syscalls/src/lib.rs (L821-857)
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
```

**File:** syscalls/src/lib.rs (L860-910)
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
```

### Title
Missing compute charge for signer-seed `Pubkey::create_program_address` hashing in CPI path allows underpriced SHA256 work - ([File: program-runtime/src/cpi.rs])

### Summary
`translate_signers` in `program-runtime/src/cpi.rs` performs up to `MAX_SIGNERS` (16) calls to `Pubkey::create_program_address`, each hashing up to `MAX_SEEDS` (16) seeds of up to `MAX_SEED_LEN` (32) bytes, but unlike the standalone `SyscallCreateProgramAddress`/`SyscallTryFindProgramAddress` syscalls, it never charges `create_program_address_units` (or any other compute) for this hashing work. `cpi_common` only charges the flat `invoke_units` once per CPI call before invoking `translate_signers`, so the SHA256 compression work performed for PDA signer verification is effectively free.

### Finding Description
In `cpi_common` (program-runtime/src/cpi.rs), the only compute charged before `translate_signers` is executed is the flat `invoke_units` cost: [1](#0-0) 
Then `translate_signers` is invoked with attacker-controlled `signers_seeds_addr`/`signers_seeds_len`: [2](#0-1) 

Inside `translate_signers`, the function bounds the signer count to `MAX_SIGNERS` (16) and each signer's seed count to `MAX_SEEDS`, then calls `Pubkey::create_program_address` once per signer without any compute-meter consumption: [3](#0-2) [4](#0-3) 

Contrast this with the direct `sol_create_program_address`/`sol_try_find_program_address` syscalls, which explicitly consume `create_program_address_units` for each `Pubkey::create_program_address` call performed: [5](#0-4) 

Because `translate_signers` calls the identical hashing primitive `Pubkey::create_program_address` up to 16 times per CPI (each over up to 16×32=512 bytes of seed data plus the program id and internal PDA marker, i.e. multiple SHA256 compression blocks), an attacker-controlled program can pass `signers_seeds_len = MAX_SIGNERS` with maximal seed counts/lengths on every CPI in a call chain up to `max_instruction_stack_depth`/`MAX_CALL_DEPTH`. Each such CPI performs real SHA256 hashing work proportional to signer count × seed bytes, but the transaction is only billed the flat `invoke_units` for the whole CPI — the seed-hashing cost is entirely unmetered. This is a genuine, reachable, per-instruction underpricing since there is no compute charge at all tied to the size/count of the `translate_signers` hashing work, not merely a flat charge that is arguably too low.

### Impact Explanation
This falls under Agave's "materially underpriced compute" bounty category: an attacker can repeatedly execute expensive SHA256 hashing (via `Pubkey::create_program_address` in the CPI signer-seed verification path) at effectively zero marginal compute cost beyond the flat per-CPI `invoke_units`, across every CPI in a transaction (bounded by `max_instruction_trace_length`/call depth). This lets adversarial programs consume disproportionate validator CPU time relative to the compute units charged, degrading replay/leader throughput without paying for it, which is a resource-exhaustion/compute-underpricing bug in `cpi_common`'s CPI processing path used during transaction replay (`core/src/replay_stage.rs` invokes program execution through this path).

### Likelihood Explanation
This is trivially and repeatably triggerable by any unprivileged user: deploy a program that repeatedly invokes CPI (`sol_invoke_signed`) with `signers_seeds_len` = `MAX_SIGNERS` and each signer seed array populated with `MAX_SEEDS` seeds of `MAX_SEED_LEN` bytes, nested to the max CPI depth. No special privileges, staked node control, or crafted snapshots are required — only normal program deployment and invocation, matching the allowed unprivileged-attacker model. The relevant limits (`MAX_SIGNERS`, `MAX_SEEDS`, `MAX_SEED_LEN`, `max_call_depth`/`MAX_CALL_DEPTH`) are all attacker-reachable constants that bound but do not eliminate the exploit.

### Recommendation
Charge `create_program_address_units` (or an equivalent, seed-size-scaled cost) in `translate_signers` for each `Pubkey::create_program_address` call it performs, mirroring the metering already present in `SyscallCreateProgramAddress`/`SyscallTryFindProgramAddress`. This should be applied via `invoke_context.compute_meter.consume_checked(...)` prior to or during the seed-hashing loop in `program-runtime/src/cpi.rs`'s `translate_signers`.

### Proof of Concept
Rust integration test plan (extending existing tests in `program-runtime/src/cpi.rs`, e.g., near `test_translate_signers`):
1. Construct a mock `InvokeContext` with `compute_meter` pre-set to only the flat `invoke_units` cost (no extra budget for hashing).
2. Call `translate_signers` with `signers_seeds_len = MAX_SIGNERS` (16), each signer's seed list containing `MAX_SEEDS` (16) seeds of `MAX_SEED_LEN` (32) bytes.
3. Assert that `translate_signers` succeeds and returns 16 valid derived addresses (or bad-seed errors from the actual derivation, not `ComputationalBudgetExceeded`), while `invoke_context.compute_meter.consume_checked` is never called with a nonzero amount tied to the seed hashing (verify via mock compute meter remaining unchanged aside from the flat `invoke_units`).
4. Compare wall-clock cost: fuzz seed count/length combinations at CPI depth `MAX_CALL_DEPTH` (nested `invoke_signed` calls), measure aggregate SHA256 hashing time versus aggregate `create_program_address_units` that would have been charged had it been metered, and assert the real cost significantly exceeds `COST_UPPER_BOUND` per compute unit charged (i.e., cost/CU for this code path is unbounded relative to `create_program_address_units`, since it is currently zero).

### Citations

**File:** program-runtime/src/cpi.rs (L60-62)
```rust
const SUCCESS: u64 = 0;
/// Maximum signers
const MAX_SIGNERS: usize = 16;
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

**File:** program-runtime/src/cpi.rs (L785-786)
```rust
    let amount = invoke_context.get_execution_cost().invoke_units;
    invoke_context.compute_meter.consume_checked(amount)?;
```

**File:** program-runtime/src/cpi.rs (L801-806)
```rust
    let signers = translate_signers(
        caller_program_id,
        signers_seeds_addr,
        signers_seeds_len,
        invoke_context,
    )?;
```

**File:** syscalls/src/lib.rs (L821-845)
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
```

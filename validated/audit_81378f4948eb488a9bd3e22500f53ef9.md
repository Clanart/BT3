### Title
CPI signer PDA derivation in `translate_signers` performs unmetered SHA256 hashing, allowing underpriced compute via repeated `create_program_address` calls per CPI - ([File: program-runtime/src/cpi.rs])

### Summary
`translate_signers` in `program-runtime/src/cpi.rs` calls `Pubkey::create_program_address` directly for every signer/seed combination supplied to a CPI (`sol_invoke_signed`), but unlike the dedicated `SyscallCreateProgramAddress`/`SyscallTryFindProgramAddress` syscalls, it never charges `create_program_address_units` (or any cost proportional to the number of seeds/bytes hashed). Only a flat `invoke_units` charge is applied per CPI in `cpi_common`, regardless of how many signer-seed sets or seed bytes are processed.

### Finding Description
`cpi_common` (program-runtime/src/cpi.rs:773-808) charges a fixed `invoke_units` amount via `invoke_context.compute_meter.consume_checked(amount)` at line 785-786, then calls `translate_signers` at line 801-806 with attacker-controlled `signers_seeds_addr`/`signers_seeds_len`.

Inside `translate_signers` (program-runtime/src/cpi.rs:631-674):
- `signers_seeds_len` is bounded only by `MAX_SIGNERS` (line 646-648).
- For each signer, `untranslated_seeds.len()` is bounded by `MAX_SEEDS` (line 658-660).
- For each seed set, `Pubkey::create_program_address(&seeds_bytes, program_id)` is invoked (line 667), which internally performs a SHA256 hash over all seed bytes plus the program ID and a PDA marker string.

No call anywhere in this path consumes `create_program_address_units` or any per-byte SHA256 cost. Contrast this with the explicit syscalls `SyscallCreateProgramAddress` and `SyscallTryFindProgramAddress` in `syscalls/src/lib.rs:821-911`, which explicitly do:
```
let cost = invoke_context.get_execution_cost().create_program_address_units;
invoke_context.compute_meter.consume_checked(cost)?;
```
before performing the identical `Pubkey::create_program_address` computation — and `SyscallTryFindProgramAddress` even charges `cost` again on every one of its 256 bump-seed iterations (syscalls/src/lib.rs:886-908).

Thus an attacker deploying an arbitrary sBPF program can invoke `sol_invoke_signed` with `signers_seeds_len` at `MAX_SIGNERS` and each signer having `MAX_SEEDS` seeds, forcing the runtime to perform up to `MAX_SIGNERS * MAX_SEEDS` PDA derivations (each a SHA256 computation) per single CPI call, all for the flat `invoke_units` cost that does not vary with `signers_seeds_len` or seed byte lengths. Repeating CPIs (e.g., in a loop, subject to `max_instruction_trace_length`/CU limit) lets an attacker perform many times more SHA256 hashing work per compute unit than the fee model intends for this operation, since the same hashing done through `sol_create_program_address` or `sol_try_find_program_address` is properly metered per call.

### Impact Explanation
This is a materially underpriced compute-unit issue: work (SHA256-based PDA derivation) that is explicitly priced elsewhere in the protocol (`create_program_address_units` per call, defaulting to 1500 CU) is not charged at all when performed identically via CPI signer-seed translation. This lets a program consume more validator CPU per compute unit purchased than intended, degrading the CU-based compute-cost invariant that block-space/CU budgeting relies on. This falls under Agave's "materially underpriced compute" bounty category rather than memory-safety/privilege-escalation, since account/privilege checks (`check_authorized_program`, `MAX_SIGNERS`, `MAX_SEEDS`) remain intact and correct — only the hashing cost is unmetered.

### Likelihood Explanation
Fully attacker-reachable with no privileges beyond deploying and invoking an arbitrary sBPF program: the attacker controls `signers_seeds_addr` and `signers_seeds_len` directly through `sol_invoke_signed`/`sol_invoke_signed_c`, and can supply `MAX_SIGNERS` signer entries each with `MAX_SEEDS` seeds of up to `MAX_SEED_LEN` bytes (mirroring the existing `translate_and_check_program_address_inputs` limits used by the priced syscalls). No feature gate or additional guard limits or prices this specific cost. The bug is deterministic and reproducible on every CPI call with maximal signer-seed structures.

### Recommendation
Charge `create_program_address_units` (or a cost scaled to the number of seeds/bytes actually hashed, consistent with `sha256_base_cost`/`sha256_byte_cost`) inside `translate_signers` for each `Pubkey::create_program_address` call it performs, mirroring the charge already applied in `SyscallCreateProgramAddress`/`SyscallTryFindProgramAddress` in `syscalls/src/lib.rs`. This charge should occur before/around the `Pubkey::create_program_address` call at cpi.rs:667, using `invoke_context.compute_meter.consume_checked(...)`, and should fail with `ComputationalBudgetExceeded` if the budget is insufficient, just as the standalone syscalls do.

### Proof of Concept
```rust
// program-runtime/src/cpi.rs (test module)
#[test]
fn test_translate_signers_unmetered_compute() {
    let transaction_accounts =
        transaction_with_one_writable_instruction_account(b"foo".to_vec());
    mock_invoke_context!(
        invoke_context,
        transaction_context,
        b"instruction data",
        transaction_accounts,
        0,
        &[1]
    );

    let program_id = Pubkey::new_unique();
    // Build MAX_SIGNERS signer-seed sets, each with MAX_SEEDS seeds of MAX_SEED_LEN bytes.
    let seed_bytes = vec![0u8; solana_pubkey::MAX_SEED_LEN];
    let seeds: Vec<&[u8]> = (0..solana_pubkey::MAX_SEEDS)
        .map(|_| seed_bytes.as_slice())
        .collect();
    let signer_sets: Vec<&[&[u8]]> = (0..MAX_SIGNERS).map(|_| seeds.as_slice()).collect();

    let vm_addr = MM_INPUT_START;
    let (_mem, region) = mock_signers(&signer_sets, vm_addr);
    let config = Config { aligned_memory_mapping: false, ..Config::default() };
    let mapping = unsafe { MemoryMapping::new(vec![region], &config, SBPFVersion::V3).unwrap() };
    invoke_context
        .memory_contexts
        .set_memory_context_abi_v1(MemoryContext::new(BpfAllocator::new(0), Vec::new(), mapping))
        .unwrap();

    // Record compute units before the call.
    let before = invoke_context.compute_meter.mock_get_remaining();

    // Even with maximal signer/seed structural limits, no compute is consumed
    // by translate_signers itself (only whatever cpi_common charges flatly elsewhere).
    let _ = translate_signers(&program_id, vm_addr, signer_sets.len() as u64, &invoke_context);

    let after = invoke_context.compute_meter.mock_get_remaining();
    // EXPECTED (fixed): after == before - (MAX_SIGNERS * create_program_address_units at minimum)
    // ACTUAL (vulnerable): after == before, i.e. zero CU consumed for
    // MAX_SIGNERS * MAX_SEEDS SHA256-based PDA derivations.
    assert_eq!(before, after, "translate_signers charges no compute for PDA derivation");
}
```
Expected result on the current code: the assertion passes, demonstrating zero compute consumption for a bounded but maximal amount of SHA256 hashing work, in contrast to equivalent work through `sol_create_program_address`/`sol_try_find_program_address`, which does deduct `create_program_address_units` per call (see `syscalls/src/lib.rs` tests `test_create_program_address`/`test_find_program_address` at syscalls/src/lib.rs:6082-6127, which assert `ComputationalBudgetExceeded` once the metered cost is exhausted).
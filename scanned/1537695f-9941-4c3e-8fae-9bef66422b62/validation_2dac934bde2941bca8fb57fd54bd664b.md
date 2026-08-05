Based on the code evidence gathered, I found a structurally analogous bug class in Agave: a security allow/deny-list check that is enforced on one code path but bypassable via an alternate path that reaches the same privileged effect — mirroring how `veto()` in the Vader report checks direct proposal actions but misses an indirect path to the same target (governance).

### Title
CPI authorization check (`check_authorized_program`) is bypassed entirely on the native-builtin CPI path - (File: `program-runtime/src/invoke_context.rs`, `program-runtime/src/cpi.rs`)

### Summary
`check_authorized_program` in `program-runtime/src/cpi.rs` is the guard that decides whether a cross-program invocation (CPI) is allowed to target `native_loader`, `bpf_loader`, `bpf_loader_deprecated`, `bpf_loader_upgradeable` (except specific carve-out instructions), or a precompile. [1](#0-0) 
This check is only invoked from the syscall CPI entrypoint `cpi_common`, which is the path used when a BPF/SBF program issues a CPI via `sol_invoke_signed`. [2](#0-1) 

### Finding Description
`InvokeContext` exposes a second, parallel CPI entrypoint, `native_invoke_signed`, intended for builtin (native, non-BPF) programs to issue CPIs on behalf of derived PDAs. [3](#0-2) 
Unlike `cpi_common`, `native_invoke_signed` calls `prepare_next_cpi_instruction` directly and never calls `check_authorized_program`: [4](#0-3) 

This is the same class of flaw as the reported issue: a restriction ("no action may target the protected contract/program") is enforced only along one call path (the veto function checking direct proposal actions; here, the syscall CPI path) while a second path that reaches the identical effect (a governance-changing action that points at governance itself; here, invoking `bpf_loader`/`bpf_loader_upgradeable`/`native_loader`/a precompile) is left unguarded. Any builtin program that calls `native_invoke_signed` with a program id and instruction data influenced by transaction/account inputs would issue that CPI without the same `check_authorized_program` gate that BPF programs are subject to.

I was not able to fully enumerate, within the available iterations, every builtin call site that invokes `native_invoke_signed` with attacker-influenced `Instruction` values (the search surfaced references in `programs/bpf_loader/src/lib.rs`, `programs/vote/src/vote_state/mod.rs`, `program-test/src/lib.rs`, `rpc/src/rpc.rs`, and `runtime/src/bank/builtins/core_bpf_migration/mod.rs`, but I could not confirm from the index whether any of those specific call sites pass through fully attacker-controlled `program_id`/`instruction_data`). This is the key uncertainty for turning this into a concretely exploitable path — the asymmetry in the guard itself is confirmed in code, but which caller(s) can drive it with attacker-controlled data needs direct source inspection (index snippets did not show the call-site bodies).

### Impact Explanation
If a builtin program can be driven (directly or transitively) to call `native_invoke_signed` with an attacker-influenced program id/instruction, the CPI-authorization allowlist/denylist in `check_authorized_program` — which exists specifically to stop CPI callers from directly manipulating `bpf_loader`/`bpf_loader_upgradeable`/`native_loader`/precompiles — would be silently skipped. Depending on which builtin/call site is reachable, this could allow instructions that the runtime intends to reserve for top-level/direct transactions (e.g. loader upgrade/set-authority/close operations, or precompile-adjacent behavior) to be issued via an unguarded internal path, undermining an invariant the runtime relies on for program-execution integrity.

### Likelihood Explanation
The asymmetry itself is unconditionally present in the code (no feature gate wraps the absence of the check in `native_invoke_signed`), so if any builtin exposes a reachable, attacker-influenced call to `native_invoke_signed`, the bypass is deterministic and requires no special validator/peer trust — only a normal transaction that reaches the relevant builtin instruction handler. Likelihood is contingent on confirming a concrete attacker-controlled call site, which I could not fully verify in this session.

### Recommendation
Move the `check_authorized_program` call (or an equivalent guard) into the shared code path used by both `cpi_common` and `native_invoke_signed` — e.g., inside `prepare_next_cpi_instruction` itself, or explicitly call `check_authorized_program(&instruction.program_id, &instruction.data, invoke_context)` at the top of `native_invoke_signed` before `prepare_next_cpi_instruction` — so the restriction is enforced uniformly regardless of whether the CPI originates from a BPF syscall or a native builtin.

### Proof of Concept
Not independently confirmed with a runnable end-to-end trigger. The verifiable evidence is the divergent guard logic itself:
- Guarded path: `cpi_common` calls `check_authorized_program` before `prepare_next_cpi_instruction`. [5](#0-4) 
- Unguarded path: `native_invoke_signed` calls `prepare_next_cpi_instruction` directly with no equivalent check. [6](#0-5) 

A full PoC would require identifying a builtin instruction handler that calls `native_invoke_signed` with a transaction-supplied `program_id`/instruction data and demonstrating it can reach `bpf_loader`/`bpf_loader_upgradeable`/`native_loader`/a precompile — this requires further source review beyond what I could complete in the given tool-call budget.

### Citations

**File:** program-runtime/src/cpi.rs (L158-182)
```rust
/// Check whether a program is authorized for CPI
fn check_authorized_program(
    program_id: &Pubkey,
    instruction_data: &[u8],
    invoke_context: &InvokeContext,
) -> Result<(), Error> {
    if native_loader::check_id(program_id)
        || bpf_loader::check_id(program_id)
        || bpf_loader_deprecated::check_id(program_id)
        || (solana_sdk_ids::bpf_loader_upgradeable::check_id(program_id)
            && !(bpf_loader_upgradeable::is_upgrade_instruction(instruction_data)
                || bpf_loader_upgradeable::is_set_authority_instruction(instruction_data)
                || (invoke_context
                    .get_feature_set()
                    .enable_bpf_loader_set_authority_checked_ix
                    && bpf_loader_upgradeable::is_set_authority_checked_instruction(
                        instruction_data,
                    ))
                || bpf_loader_upgradeable::is_close_instruction(instruction_data)))
        || invoke_context.is_precompile(program_id)
    {
        return Err(Box::new(CpiError::ProgramNotSupported(*program_id)));
    }
    Ok(())
}
```

**File:** program-runtime/src/cpi.rs (L796-808)
```rust
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
    check_authorized_program(&instruction.program_id, &instruction.data, invoke_context)?;
    invoke_context.prepare_next_cpi_instruction(instruction, &signers)?;
```

**File:** program-runtime/src/invoke_context.rs (L319-345)
```rust
    /// Entrypoint for a cross-program invocation from a builtin program.
    ///
    /// Takes signer seeds and derives PDAs internally via
    /// `create_program_address`, mirroring the SBF CPI path. This makes
    /// it structurally impossible for a builtin to vouch for a non-PDA
    /// address (e.g. a user wallet) as a signer.
    pub fn native_invoke_signed(
        &mut self,
        instruction: Instruction,
        signer_seeds: &[&[&[u8]]],
    ) -> Result<(), InstructionError> {
        let caller_program_id = *self
            .transaction_context
            .get_current_instruction_context()?
            .get_program_key()?;
        // The conversion from `PubkeyError` to `InstructionError` through
        // num-traits is incorrect, but it's the existing behavior.
        let signers = signer_seeds
            .iter()
            .map(|seeds| Pubkey::create_program_address(seeds, &caller_program_id))
            .collect::<Result<Vec<Pubkey>, solana_pubkey::PubkeyError>>()
            .map_err(|e| e as u64)?;
        self.prepare_next_cpi_instruction(instruction, &signers)?;
        let mut compute_units_consumed = 0;
        self.process_instruction(&mut compute_units_consumed, &mut ExecuteTimings::default())?;
        Ok(())
    }
```

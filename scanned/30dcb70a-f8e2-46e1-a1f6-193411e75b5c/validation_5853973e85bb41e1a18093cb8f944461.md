## Analysis

The Lambo report's core primitive is: **a deterministic future address + a single "already exists" guard that any unprivileged actor can trip early, producing a permanent (non-recoverable-by-retry) failure of legitimate account creation.** Agave has this exact primitive natively in the System Program, and — tellingly — Agave engineers have already recognized it as a real defect and shipped a feature-gated workaround (`CreateAccountAllowPrefund`, SIMD-0312) that is *not* the default path used by most existing callers, including built-in Agave code.

### Title
Unprivileged lamport-dust griefing permanently blocks `SystemInstruction::CreateAccount` at any pre-computable deterministic address (PDA) - (File: `programs/system/src/system_processor.rs`)

### Summary
`system_processor::create_account` rejects account creation whenever the destination account already holds any lamports, returning `SystemError::AccountAlreadyInUse`. Any unprivileged party can send a trivial `Transfer` of 1 lamport to a program-derived address (PDA) before its owning program ever tries to create it, since PDAs are fully deterministic (`Pubkey::find_program_address`) and computable off-chain by anyone. This permanently blocks the legitimate `create_account` CPI for that specific address — mirroring the Lambo `createPair` front-run DoS, where a deterministic address is claimed before the legitimate initializer can use it.

### Finding Description
`create_account` in `programs/system/src/system_processor.rs` performs this check before creating an account: [1](#0-0) 

Any account with `lamports() > 0` unconditionally fails with `AccountAlreadyInUse`, regardless of whether the account has an owner or data — a bare `system_instruction::transfer` of 1 lamport is sufficient to poison the address. Because PDAs are derived from public seeds (`find_program_address`), an attacker does not need to observe any pending transaction; they can pre-compute the address entirely off-chain and send the poisoning transfer at any time before the legitimate program executes its `create_account` CPI, at which point the CPI fails with `InstructionError` bubbled from `AccountAlreadyInUse` and the entire enclosing transaction is aborted. There is no "already exists, just don't re-create it" fallback like the recommended Uniswap mitigation — the CPI simply fails every time it's retried, because the poisoned lamports remain on the account and can't be swept elsewhere without control of the recipient authority for that address.

Agave's own engineers have already identified this exact defect class and introduced a fix, but it is *opt-in* and not what `create_account` (the path virtually every program, including Agave's own `bpf_loader`, uses) executes: [2](#0-1) 

This new instruction is feature gated: [3](#0-2) [4](#0-3) 

An in-tree consumer that still uses the vulnerable `create_account` path against a fully deterministic PDA is the upgradeable BPF loader's `DeployWithMaxDataLen` handler, which derives the ProgramData account address purely from the (attacker-visible) new program ID and creates it via `system_instruction::create_account`: [5](#0-4) 

Any unprivileged actor who learns the intended `new_program_id` (e.g. from a published deployment plan, vanity keypair, or simply by observing the account being funded prior to deploy) can pre-transfer 1 lamport to the derived `programdata_key` and permanently prevent that specific deploy from succeeding via `create_account`.

### Impact Explanation
This is a genuine non-RPC, unprivileged, remote DoS primitive baked into a core Agave built-in program (System Program) that every other program — native or BPF — relies on for account initialization. Because the check depends only on lamports > 0 with no distinction between "griefed with dust" and "legitimately in use," and because `create_account` (not the new opt-in `CreateAccountAllowPrefund`) remains the default/only path for the vast majority of existing on-chain programs (including Agave's own `bpf_loader`), any protocol that derives a PDA/initialization address from public, precomputable seeds is permanently exposed to this griefing vector until the world migrates to `CreateAccountAllowPrefund`. The impact is availability loss (permanent inability to initialize a specific deterministic account) for the affected address, which can cascade into stuck protocol state (e.g., un-initializable vaults, pools, or program deployments) — directly analogous to the "LamboFactory permanently DoS-ed" impact in the source report.

### Likelihood Explanation
High for any protocol using system-program `create_account` against a fully public/precomputable address (which is the majority pattern for PDAs). The attack costs a single `Transfer` instruction and 1 lamport, requires no special privilege, no validator collusion, and no observation of a specific pending transaction (unlike a strict front-run) — only knowledge of the seeds, which are by design public/derivable. The existence of `create_account_allow_prefund` (SIMD-0312) in this codebase is itself evidence that Agave engineers consider this a real, worth-fixing issue; the residual risk is that the fix is opt-in and does not retroactively protect existing `create_account` call sites such as `programs/bpf_loader/src/lib.rs`.

### Recommendation
- For any Agave built-in/native program still calling `system_instruction::create_account` against a deterministic address (e.g., `programs/bpf_loader/src/lib.rs`'s ProgramData creation), migrate to `SystemInstruction::CreateAccountAllowPrefund` once broadly activated, or perform an explicit "already funded but not yet owned" check-and-continue instead of hard-failing.
- Consider making the dust-prefund tolerance the default behavior of `CreateAccount` (behind an appropriately staged feature) rather than a separate opt-in instruction, since the vulnerable check in [6](#0-5)  is what most on-chain programs still invoke.
- Document the risk for BPF program authors so they use `Allocate`+`Assign` (which only checks data/owner, not lamports) plus manual lamport top-up instead of `CreateAccount` when initializing addresses derived from public seeds.

### Proof of Concept
1. Off-chain, compute a target PDA `P = find_program_address(seeds, target_program)` that a victim program intends to initialize later (e.g., the `programdata_key` in `programs/bpf_loader/src/lib.rs` line 280-281, or any application-level vault/ATA-style PDA).
2. Submit an ordinary `SystemInstruction::Transfer` of 1 lamport to `P` from any funded, unprivileged keypair. No signature from `P` is required since `P` is only the destination of a transfer.
3. When the victim's transaction later invokes `system_instruction::create_account` (directly or via CPI) targeting `P`, `system_processor::create_account` executes the check at [6](#0-5)  and returns `SystemError::AccountAlreadyInUse`, aborting the transaction.
4. Because the poisoning lamports remain on `P` (no one holds signing authority over `P` to reclaim/reassign them), every subsequent retry of the same `create_account` instruction fails identically — a permanent DoS on initializing that specific deterministic address.

### Citations

**File:** programs/system/src/system_processor.rs (L160-174)
```rust
) -> Result<(), InstructionError> {
    // if it looks like the `to` account is already in use, bail
    {
        let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
        if to.get_lamports() > 0 {
            ic_msg!(
                invoke_context,
                "Create Account: account {:?} already in use",
                to_address
            );
            return Err(SystemError::AccountAlreadyInUse.into());
        }

        allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
    }
```

**File:** programs/system/src/system_processor.rs (L184-214)
```rust
/// Create a new account without checking for 0 lamports. All other checks remain.
/// Intended for use where account has already had rent paid in whole or in part
/// before creation.
#[allow(clippy::too_many_arguments)]
fn create_account_allow_prefund(
    to_account_index: IndexOfAccount,
    to_address: &Address,
    from_and_lamports: Option<(IndexOfAccount, u64)>,
    space: u64,
    owner: &Pubkey,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    {
        let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
        allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
    }
    if let Some((from_account_index, lamports)) = from_and_lamports
        && lamports > 0
    {
        transfer(
            from_account_index,
            to_account_index,
            lamports,
            invoke_context,
            instruction_context,
        )?;
    }
    Ok(())
}
```

**File:** programs/system/src/system_processor.rs (L530-547)
```rust
        SystemInstruction::CreateAccountAllowPrefund {
            lamports,
            space,
            owner,
        } => {
            if !invoke_context
                .get_feature_set()
                .create_account_allow_prefund
            {
                return Err(InstructionError::InvalidInstructionData);
            }
            let from_and_lamports = if lamports > 0 {
                instruction_context.check_number_of_instruction_accounts(2)?;
                Some((1, lamports))
            } else {
                instruction_context.check_number_of_instruction_accounts(1)?;
                None
            };
```

**File:** feature-set/src/lib.rs (L2463-2466)
```rust
        (
            create_account_allow_prefund::id(),
            "SIMD-0312: Enable CreateAccountAllowPrefund system program instruction",
        ),
```

**File:** programs/bpf_loader/src/lib.rs (L279-311)
```rust
            // Create ProgramData account
            let (derived_address, bump_seed) =
                Pubkey::find_program_address(&[new_program_id.as_ref()], program_id);
            if derived_address != programdata_key {
                ic_logger_msg!(log_collector, "ProgramData address is not derived");
                return Err(InstructionError::InvalidArgument);
            }

            // Drain the Buffer account to payer before paying for programdata account
            {
                let mut buffer = instruction_context.try_borrow_instruction_account(3)?;
                let mut payer = instruction_context.try_borrow_instruction_account(0)?;
                payer.checked_add_lamports(buffer.get_lamports())?;
                buffer.set_lamports(0)?;
            }

            let owner_id = *program_id;
            let mut instruction = system_instruction::create_account(
                &payer_key,
                &programdata_key,
                1.max(rent.minimum_balance(programdata_len)),
                programdata_len as u64,
                program_id,
            );

            // pass an extra account to avoid the overly strict UnbalancedInstruction error
            instruction
                .accounts
                .push(AccountMeta::new(buffer_key, false));

            invoke_context
                .native_invoke_signed(instruction, &[&[new_program_id.as_ref(), &[bump_seed]]])?;

```

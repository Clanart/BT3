## Analysis

The Linea bug's core pattern — **an unprivileged actor pre-occupying a deterministic address before the legitimate owner uses it, permanently blocking the legitimate operation** — has a direct analog in Agave's BPF Loader Upgradeable program deployment path.

### Title
Program deployment via `DeployWithMaxDataLen` can be permanently DoS'd by pre-funding the deterministic ProgramData PDA - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
`UpgradeableLoaderInstruction::DeployWithMaxDataLen` creates the `ProgramData` account by invoking the System Program's plain `create_account` at a **deterministic** address derived solely from the (attacker-observable) program pubkey. Because the System Program's `create_account` fails whenever the destination already holds `lamports > 0`, any unprivileged party can send a single-lamport `Transfer` to that PDA before the deployer's transaction lands, permanently blocking that specific program ID from ever being deployed.

### Finding Description
In `programs/bpf_loader/src/lib.rs`, the ProgramData address is derived deterministically: [1](#0-0) 

It is then created via the System Program's ordinary `create_account` CPI (not the prefund-tolerant variant): [2](#0-1) 

The System Program's `create_account` implementation explicitly rejects any destination that already has a positive lamport balance: [3](#0-2) 

This is exactly the invariant assumed broken in the Linea report: the protocol assumes an address is "unused" until the legitimate owner claims it, but an attacker can pre-fund it first via an ordinary `SystemInstruction::Transfer` (no signature from the target needed, since transfers only require the sender to sign) to make `to.get_lamports() > 0` before the legitimate `DeployWithMaxDataLen` executes. The `programdata_key` is `Pubkey::find_program_address(&[new_program_id.as_ref()], program_id)` — fully computable by anyone who learns the intended `new_program_id` (e.g., from a broadcast/pending transaction, a published vanity keypair, or mempool observation), just as the Linea attacker could pre-deploy at a `create2` address once they knew it.

Notably, Agave already has a mitigation for this exact "prefund squatting" pattern elsewhere: the System Program's `CreateAccountAllowPrefund` instruction is designed specifically to tolerate a pre-funded destination account: [4](#0-3) 

and the CoreBPF migration path explicitly documents and permits "prefunded" program-data accounts: [5](#0-4) 

However, `DeployWithMaxDataLen` — the code path used for *ordinary, unprivileged, first-time program deployment* — was never updated to use `create_account_allow_prefund`; it still calls the plain, prefund-intolerant `system_instruction::create_account`. This mirrors the Linea situation precisely: a "solution" pattern (tolerating pre-existing state at a deterministic address) exists in the codebase, but is not applied uniformly, leaving one specific instruction path (`DeployWithMaxDataLen`) exploitable.

### Impact Explanation
Any user attempting to deploy a program via `bpf_loader_upgradeable::deploy_with_max_program_len` (loader v3 "DeployWithMaxDataLen") can be permanently blocked from deploying to their intended, pre-chosen program address. Because `program_id` is normally a fixed keypair chosen and often publicly announced ahead of time (vanity addresses, published program IDs, on-chain buffer setup transactions visible before the final deploy transaction lands), an attacker only needs to send 1 lamport to the derived `programdata_key` before the deploy transaction executes. The victim cannot recover that specific program address for deployment — since `ProgramData` address derivation is fixed by `program_id`, there is no way to “retry” at the same address; the developer must discard the program keypair entirely and redeploy under a new pubkey. This is a non-privileged, remotely triggerable griefing/DoS against a core Agave built-in program (`bpf_loader_upgradeable`) — no elevated privileges, malicious validator, or trusted role is needed, only a simple `Transfer` instruction from an ordinary account.

### Likelihood Explanation
High. The attack requires only:
1. Knowledge of the target `program_id` (public in essentially all deployment flows, since the CLI/SDK stashes a `Program` account and buffer beforehand, and program IDs are commonly shared before final deployment).
2. A single ordinary `SystemInstruction::Transfer` transaction sending 1 lamport to the derived PDA — no special permissions, signatures from the victim, or race-condition sophistication beyond simple front-running (submit the transfer before the deploy transaction, e.g., by observing pending buffer-write transactions in the mempool/gossip).

### Recommendation
Update the `DeployWithMaxDataLen` handler in `programs/bpf_loader/src/lib.rs` to use the prefund-tolerant `SystemInstruction::CreateAccountAllowPrefund` (already implemented at `programs/system/src/system_processor.rs`) instead of the plain `create_account`, mirroring the approach already used by the CoreBPF migration path (`allow_prefunded` in `runtime/src/bank/builtins/core_bpf_migration/target_bpf_v2.rs`). This allows the ProgramData PDA to tolerate a pre-existing lamport balance while still enforcing ownership/data-emptiness checks, closing the griefing vector.

### Proof of Concept
1. Developer generates a program keypair `P` and begins deployment: uploads program bytes to a `Buffer` account, and creates/funds the uninitialized `Program` account at `P` (both are visible on-chain/in the mempool before the final `DeployWithMaxDataLen` transaction).
2. Attacker computes `programdata_key = Pubkey::find_program_address(&[P.as_ref()], bpf_loader_upgradeable::id())` — a fully deterministic address requiring only `P`, which is already public at this point.
3. Attacker submits a plain `SystemInstruction::Transfer` sending 1 lamport to `programdata_key`, landing before the developer's `DeployWithMaxDataLen` transaction.
4. When the developer's `DeployWithMaxDataLen` executes, it invokes `system_instruction::create_account` for `programdata_key` [6](#0-5) , which fails with `SystemError::AccountAlreadyInUse` because `to.get_lamports() > 0` [7](#0-6) .
5. The deployment permanently fails for program keypair `P`; the developer must discard `P` and generate an entirely new program address to deploy successfully.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L280-285)
```rust
            let (derived_address, bump_seed) =
                Pubkey::find_program_address(&[new_program_id.as_ref()], program_id);
            if derived_address != programdata_key {
                ic_logger_msg!(log_collector, "ProgramData address is not derived");
                return Err(InstructionError::InvalidArgument);
            }
```

**File:** programs/bpf_loader/src/lib.rs (L295-310)
```rust
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

**File:** runtime/src/bank/builtins/core_bpf_migration/target_bpf_v2.rs (L47-61)
```rust
        let program_data_address = get_program_data_address(program_address);

        let program_data_account_lamports = if allow_prefunded {
            // The program data account should not exist, but a system account with funded
            // lamports is acceptable.
            if let Some(account) = bank.get_account_with_fixed_root(&program_data_address) {
                if account.owner() != &SYSTEM_PROGRAM_ID {
                    return Err(CoreBpfMigrationError::ProgramHasDataAccount(
                        *program_address,
                    ));
                }
                account.lamports()
            } else {
                0
            }
```

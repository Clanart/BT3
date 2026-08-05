## Title
BPF Loader Upgradeable `DeployWithMaxDataLen` is DoS-able by pre-funding the derived ProgramData PDA - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
The reported bug is a generic "predictable derived-address pre-funding" DoS: an attacker computes a deterministic PDA in advance and sends it a trivial amount of lamports so that a subsequent privileged `system_instruction::create_account` call fails with `already in use`, permanently blocking the victim's create/deposit path. The exact same primitive exists in Agave's own `bpf_loader_upgradeable` program when it handles `UpgradeableLoaderInstruction::DeployWithMaxDataLen`.

### Finding Description
In `DeployWithMaxDataLen`, the ProgramData address is derived deterministically from the (already-known) program id: [1](#0-0) 

and it is then created via the strict `system_instruction::create_account`, which is invoked signed by the loader itself: [2](#0-1) 

This call goes through the system program's `create_account` handler, which rejects creation whenever the destination account already carries any lamports: [3](#0-2) 

Because `find_program_address(&[new_program_id.as_ref()], program_id)` is fully deterministic, anyone who observes the `new_program_id` (revealed as soon as the associated Program account/buffer becomes visible on-chain, well before the atomic `DeployWithMaxDataLen` instruction lands) can compute the exact ProgramData PDA off-chain and send it 1 lamport ahead of time. When the legitimate deploy transaction then executes, the `create_account` call inside the loader will always return `SystemError::AccountAlreadyInUse`, aborting the whole instruction.

Agave already recognizes this exact class of problem elsewhere in the codebase: the system program was extended with a dedicated `CreateAccountAllowPrefund` instruction (via `create_account_allow_prefund`) specifically to tolerate pre-funded destination accounts and avoid the "already in use" DoS: [4](#0-3) [5](#0-4) 

However, `bpf_loader_upgradeable`'s `DeployWithMaxDataLen` path was never migrated to use this prefund-tolerant primitive — it still calls the strict `create_account`, so the mitigation that exists for the general primitive is not applied to this specific, security-relevant, in-tree caller.

### Impact Explanation
This is a targeted denial-of-service against program deployment: for a specific chosen `new_program_id` keypair, the deployer's `DeployWithMaxDataLen` transaction will permanently fail once the ProgramData PDA is griefed, since the address is fixed by `new_program_id` and can never be "fixed" without generating a brand-new program keypair (and re-uploading the whole buffer, re-paying rent for the buffer, etc.). No funds are stolen and nothing is permanently locked (the instruction — including the earlier buffer-draining step at lines 287-293 — fails atomically and rolls back), matching the "Medium/no fund loss, permanent DoS for the specific derived address" profile of the original report.

### Likelihood Explanation
High. The attack requires only: (1) observing/predicting the `new_program_id` used for deployment (visible from mempool or any prior public transaction that references the program keypair, e.g. buffer setup steps), (2) computing `find_program_address(&[new_program_id], bpf_loader_upgradeable::id())` locally, and (3) sending a 1-lamport system transfer to that address before the deploy transaction is confirmed. This needs no special privileges, no validator collusion, and can be repeated for any target program id.

### Recommendation
Change the loader's internal `create_account` call in `DeployWithMaxDataLen` (and any other loader-internal PDA creation using the strict `CreateAccount`) to use the prefund-tolerant `CreateAccountAllowPrefund` instruction/helper that Agave already introduced in `programs/system/src/system_processor.rs`, or explicitly check/tolerate a pre-existing lamports balance on the derived ProgramData account before calling `create_account`, analogous to Anchor's `init_if_needed` recommendation in the source report.

### Proof of Concept
1. Deployer generates a new program keypair `new_program_id` and begins the standard deploy flow (create+write Buffer account for the program bytes). This step makes `new_program_id` publicly observable (it appears as an account in transactions/mempool).
2. Attacker computes `programdata_key, bump = Pubkey::find_program_address(&[new_program_id.as_ref()], bpf_loader_upgradeable::id())`.
3. Attacker sends a 1-lamport `SystemInstruction::Transfer` to `programdata_key` before the deployer's `DeployWithMaxDataLen` transaction lands.
4. Deployer's `DeployWithMaxDataLen` executes; inside it, `system_instruction::create_account(&payer_key, &programdata_key, ...)` is invoked via `invoke_context.native_invoke_signed`, hits the `to.get_lamports() > 0` check in `system_processor::create_account`, and returns `SystemError::AccountAlreadyInUse`, aborting the whole deploy instruction permanently for that `new_program_id`.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L279-285)
```rust
            // Create ProgramData account
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

**File:** programs/system/src/system_processor.rs (L160-172)
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

**File:** programs/system/src/system_processor.rs (L530-563)
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
            let to_address = Address::create(
                instruction_context.get_key_of_instruction_account(0)?,
                None,
                invoke_context,
            )?;
            create_account_allow_prefund(
                0,
                &to_address,
                from_and_lamports,
                space,
                &owner,
                &signers,
                invoke_context,
                &instruction_context,
            )
        }
```

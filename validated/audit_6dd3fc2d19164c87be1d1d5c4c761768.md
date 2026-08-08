## Title
Front-runnable deterministic ProgramData PDA can permanently DoS initial BPF Upgradeable program deployment - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
`bpf_loader_upgradeable`'s `DeployWithMaxDataLen` handler derives the ProgramData account address deterministically via `Pubkey::find_program_address(&[new_program_id.as_ref()], program_id)` and then invokes `system_instruction::create_account` for that exact address. `system_processor::create_account` unconditionally rejects creation if the target account already has any lamports (`AccountAlreadyInUse`). Because the ProgramData PDA is fully computable off-chain from the not-yet-deployed program's public key, an unprivileged user can pre-fund that address with a trivial System `Transfer` before the legitimate deploy transaction lands, permanently blocking that specific deployment — the same "pre-create at a deterministic address to force a deploy-time existence-check revert" pattern described in the LamboFactory/Uniswap report.

### Finding Description
In `programs/bpf_loader/src/lib.rs`, the `DeployWithMaxDataLen` path computes: [1](#0-0) 
and then unconditionally attempts to create the account at that address via a native invocation of the System Program: [2](#0-1) 

`system_processor::create_account` (invoked here) fails hard if the destination already holds lamports, with no path to recover: [3](#0-2) 

`new_program_id` (the program's own pubkey, typically an ephemeral keypair chosen by the deployer, e.g. via `cli/src/program.rs`'s deploy flow) is public as soon as the deploy transaction is submitted/observed, and the ProgramData PDA derived from it is computable by anyone using `find_program_address`. This mirrors the LamboFactory bug class exactly: a deterministic, precomputable address is checked for "already exists" before use, and any third party can win the race by simply sending lamports to that address (a plain unauthenticated System `Transfer`, requiring no signature from the target) before the real creation transaction executes.

The `test_create_already_in_use` unit test in the System Program confirms that even 1 lamport of pre-funding is sufficient to force `AccountAlreadyInUse`: [4](#0-3) 

### Impact Explanation
Any observer who sees an in-flight (or even just publicly known/pre-announced) program-deploy transaction targeting program pubkey `P` can compute `programdata = find_program_address([P], bpf_loader_upgradeable::id())` and front-run with a 1-lamport transfer to that address. When the legitimate `DeployWithMaxDataLen` instruction later executes, the nested `system_instruction::create_account` call reverts with `AccountAlreadyInUse`, and the whole deploy transaction fails. Because the PDA is a deterministic function of `P` and the loader program id, this failure is permanent for that specific `P` — the account can never be "un-funded" by the victim (they don't control it, it's a PDA with no keypair), so the only remedy is to abandon that program pubkey and redeploy under a fresh one. This is a real griefing/DoS vector against unprivileged deploy flows, though its blast radius is narrower than the original LamboFactory report (which poisoned an ever-incrementing, fully deterministic sequence so *every* future deployment was blocked); here, only the specific targeted program address is denied, and the deployer can choose a new arbitrary keypair for a retry.

### Likelihood Explanation
Moderate. It requires an attacker to learn the target program pubkey before the `DeployWithMaxDataLen` instruction is confirmed (e.g., by observing the transaction in flight, or because program addresses are often announced/vanity-generated ahead of time) and to submit a cheap, unprivileged `Transfer` that lands first. No special privileges, precompiled proofs, or validator/operator role are needed — a normal fee-paying transaction suffices.

### Recommendation
Before invoking `system_instruction::create_account` for the ProgramData PDA in `programs/bpf_loader/src/lib.rs`, check whether the account already exists/has a nonzero balance and, if so, use a "top-up + assign" path instead of unconditionally failing — analogous to `create_account_allow_prefund`/`CreateAccountAllowPrefund`, which already exists in `programs/system/src/system_processor.rs` specifically to tolerate pre-funded destination accounts: [5](#0-4) 
Having the BPF loader route ProgramData account creation through this prefund-tolerant path (rather than the strict `create_account`) would close this front-running/DoS vector for initial program deployments, mirroring the "check existence, don't unconditionally fail" mitigation recommended in the original report.

### Proof of Concept
1. Attacker observes/derives that a deployer intends to deploy a program at pubkey `P` (e.g., sees the pending `DeployWithMaxDataLen` transaction, or the program keypair is announced ahead of time).
2. Attacker computes `programdata = Pubkey::find_program_address(&[P.as_ref()], &bpf_loader_upgradeable::id())` (same derivation as `programs/bpf_loader/src/lib.rs:280-285`).
3. Attacker submits a plain System `Transfer` (or `CreateAccount` with 1 lamport, any unprivileged instruction) sending `≥1` lamport to `programdata`, landing before the victim's deploy transaction.
4. Victim's `DeployWithMaxDataLen` instruction executes; its internal `native_invoke_signed` call to `system_instruction::create_account` for `programdata` now hits `to.get_lamports() > 0` and returns `SystemError::AccountAlreadyInUse` per `programs/system/src/system_processor.rs:160-171`, causing the entire deploy transaction to fail.
5. Because `programdata` is a PDA with no controlling keypair, the victim cannot reclaim/reuse it and must generate a new program pubkey to retry deployment.

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

**File:** programs/bpf_loader/src/lib.rs (L296-310)
```rust
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

**File:** programs/system/src/system_processor.rs (L160-171)
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

**File:** programs/system/src/system_processor.rs (L950-984)
```rust
    #[test]
    fn test_create_already_in_use() {
        let new_owner = Pubkey::from([9; 32]);
        let from = Pubkey::new_unique();
        let from_account = AccountSharedData::new(100, 0, &system_program::id());
        let owned_key = Pubkey::new_unique();

        // Attempt to create system account in account already owned by another program
        let original_program_owner = Pubkey::from([5; 32]);
        let owned_account = AccountSharedData::new(0, 0, &original_program_owner);
        let unchanged_account = owned_account.clone();
        let accounts = process_instruction(
            &bincode::serialize(&SystemInstruction::CreateAccount {
                lamports: 50,
                space: 2,
                owner: new_owner,
            })
            .unwrap(),
            vec![(from, from_account.clone()), (owned_key, owned_account)],
            vec![
                AccountMeta {
                    pubkey: from,
                    is_signer: true,
                    is_writable: false,
                },
                AccountMeta {
                    pubkey: owned_key,
                    is_signer: true,
                    is_writable: false,
                },
            ],
            Err(SystemError::AccountAlreadyInUse.into()),
        );
        assert_eq!(accounts[0].lamports(), 100);
        assert_eq!(accounts[1], unchanged_account);
```

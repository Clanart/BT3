## Title
Program deployment (`DeployWithMaxDataLen`) DoS via ProgramData PDA pre-funding — (File: `programs/bpf_loader/src/lib.rs`)

## Summary
The reported Uniswap bug class is: an attacker pre-manipulates a *derived/predictable* balance slot before a privileged automated operation runs a strict "must be pristine" check, causing that operation to permanently revert (DoS). The reachable Solana analog is the `DeployWithMaxDataLen` instruction in the upgradeable BPF loader, which derives the `ProgramData` account address deterministically from the (public) new program's pubkey and then invokes the System Program's `CreateAccount`, which unconditionally fails if the target address already holds any lamports.

## Finding Description
When a program is deployed for the first time via `UpgradeableLoaderInstruction::DeployWithMaxDataLen`, the loader computes the `ProgramData` account address as a PDA derived solely from the new program's public key: [1](#0-0) 

It then issues a `system_instruction::create_account` CPI for that exact derived address to fund and initialize it: [2](#0-1) 

The System Program's `create_account` handler enforces a strict precondition — the destination account must currently hold zero lamports, otherwise it unconditionally errors out with `AccountAlreadyInUse`: [3](#0-2) 

Because the `programdata_key` is fully deterministic from the (soon to be public) program pubkey, any unprivileged attacker who observes the pending deploy transaction (e.g., via gossip/transaction forwarding prior to confirmation, or via a leaked/pre-announced program keypair) can send an arbitrarily small transfer of lamports to that PDA in an earlier or same-slot transaction. When the legitimate `DeployWithMaxDataLen` instruction later executes, `create_account` fails the "already in use" check, causing the entire deployment instruction — and therefore the transaction — to fail with `InstructionError::AccountAlreadyInUse`/`IncorrectProgramId`, exactly mirroring the "sync() pre-funds the pair, then the strict `addLiquidityETH` minOut check reverts" DoS pattern in the external report. Because the destination address is address-space-derived and immutable for that program pubkey, the deployer cannot resubmit with the same program id — every retry hits the same poisoned PDA and fails identically, unless the deployer picks an entirely new program pubkey.

Notably, the codebase already recognizes and has begun mitigating this exact bug class elsewhere: it introduces a new `SystemInstruction::CreateAccountAllowPrefund` / `create_account_allow_prefund` path specifically to tolerate pre-funded destination accounts: [4](#0-3) 

However, the `DeployWithMaxDataLen` path in the BPF loader has not been migrated to use this prefund-tolerant variant, and instead retains the original strict `create_account` call, leaving initial program deployment exposed to this griefing/DoS vector.

## Impact Explanation
An unprivileged attacker can permanently block a specific program from ever being deployed at a chosen address by front-running the deployer's `DeployWithMaxDataLen` transaction with a 1-lamport transfer to the deterministically-derivable `ProgramData` PDA. This is a targeted, low-cost denial-of-service against program deployment — the deployer's transaction (and any retry using the same program keypair) will deterministically fail, forcing the victim to generate and re-announce an entirely new program keypair, which can itself be repeatedly griefed if the address becomes known before landing on-chain.

## Likelihood Explanation
Likelihood is bounded by whether the attacker can learn the target program pubkey before the deploy transaction is confirmed (e.g., via transaction propagation/gossip visibility, a leaked keypair, or a publicly pre-announced vanity program address). Given Solana's gossip-based transaction forwarding, an attacker able to observe unconfirmed transactions in the relevant leader/validator flow can react and front-run with a trivial single-lamport transfer, making exploitation cheap once the address is known.

## Recommendation
Update the `DeployWithMaxDataLen` handler in `programs/bpf_loader/src/lib.rs` to use the prefund-tolerant `create_account_allow_prefund` system instruction (already implemented in `programs/system/src/system_processor.rs`) for the `ProgramData` account creation, rather than the strict `create_account`, so that any lamports an attacker deposits ahead of time are absorbed instead of causing failure. Alternatively, perform a `skim`/reconciliation step (transfer out any pre-existing lamports to the payer, similar to the Buffer-draining pattern already used a few lines above) before invoking `create_account`.

## Proof of Concept
1. Attacker observes (or is informed of) the program pubkey `P` about to be used in an upcoming `DeployWithMaxDataLen` transaction.
2. Attacker computes `programdata_key = find_program_address([P], bpf_loader_upgradeable::id())` and submits a transaction transferring 1 lamport to `programdata_key` before the deployer's transaction lands.
3. The deployer's `DeployWithMaxDataLen` transaction executes `system_instruction::create_account` targeting `programdata_key`; since its lamport balance is now `1 > 0`, `system_processor::create_account` (`programs/system/src/system_processor.rs:160-171`) returns `SystemError::AccountAlreadyInUse`, failing the whole deploy instruction.
4. Every subsequent deploy attempt using program pubkey `P` fails identically, since `programdata_key` is immutable for that program id.

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

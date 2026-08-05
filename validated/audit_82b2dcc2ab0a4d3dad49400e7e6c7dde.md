## Title
Permissionless lamport-dusting griefs `SystemInstruction::CreateAccount` / `CreateAccountWithSeed` via the `to.get_lamports() > 0` check - ([File: programs/system/src/system_processor.rs])

### Summary
The reported CNote bug is an exact-balance ("==0") post-condition check that can be broken by anyone donating funds to the target address, causing legitimate operations to permanently revert. Agave's `create_account` function in the System Program contains the mirror-image invariant: it requires the destination account to have **zero lamports** before allowing creation, and any unprivileged account can push lamports to an arbitrary, not-yet-created pubkey via a plain `Transfer` instruction, permanently breaking that precondition.

### Finding Description
`create_account` in `programs/system/src/system_processor.rs` enforces: [1](#0-0) 

```rust
fn create_account(
    ...
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
    transfer(...)
}
```

Both `SystemInstruction::CreateAccount` and `SystemInstruction::CreateAccountWithSeed` dispatch to this function: [2](#0-1) 

The check is a strict `> 0` (equivalent to the CNote `== 0` post-check, just inverted direction): the account must have **exactly zero** lamports for creation to proceed. Because the System Program's `Transfer` instruction lets any signer send lamports to **any** pubkey with no ownership or existence requirements on the recipient (`transfer_verified` at lines 216-243 only checks the sender), an attacker can pre-fund (dust) any not-yet-created target address with as little as 1 lamport before the legitimate owner ever submits their `CreateAccount` transaction. Every subsequent attempt to create that account with `SystemInstruction::CreateAccount`/`CreateAccountWithSeed` then fails with `SystemError::AccountAlreadyInUse`, indefinitely, since nothing in the protocol lets the intended owner reclaim or zero-out lamports on an account they don't yet control.

Notably, the codebase already contains a fix pattern for this exact problem — `create_account_allow_prefund`, which skips the zero-lamports check for cases where prefunding is expected — but it is not used by the general `SystemInstruction::CreateAccount` / `CreateAccountWithSeed` path: [3](#0-2) 

This shows the maintainers are aware that pre-funding causes CreateAccount failures, but the generic instruction handlers still use the strict, griefable `create_account`.

### Impact Explanation
Any address that is meant to be created later via `CreateAccount`/`CreateAccountWithSeed` (e.g., a freshly-derived keypair, a PDA-with-seed nonce/stake/vote account, or any deterministically-computed address a dApp/user plans to initialize) can be permanently denied by an unprivileged attacker for the cost of one lamport transfer. This is a pure griefing/DoS vector on account initialization: no malicious validator, leader, or trusted role is required — a single ordinary `Transfer` transaction from any funded wallet suffices, and it can be executed proactively (no race/front-run timing needed) against any address whose pubkey or seed-derivation the attacker can predict or observe.

### Likelihood Explanation
Likelihood is high for any deterministic/derivable target address (e.g. `CreateAccountWithSeed`, well-known program-derived addresses used by clients before creation) because the attacker doesn't need to win a race — they can dust the address at any point before the real creation transaction lands. For fully random keypairs generated client-side and never revealed until the creation transaction is broadcast, the attack requires observing the pending transaction (mempool/gossip visibility) to extract the target pubkey and front-run it, which is the weaker case.

### Recommendation
Follow the pattern already established by `create_account_allow_prefund`: replace the strict `to.get_lamports() > 0` rejection in `create_account` with a check that only rejects if the account is already allocated (i.e. has non-empty data or a non-system owner), and treat any pre-existing lamports as an implicit partial prefund to be topped up by the `transfer` call, mirroring the CNote fix recommendation of "use balance differences instead of an equality/zero check."

### Proof of Concept
1. Client A intends to create account `P` (either a random keypair whose pubkey it has generated, or a `create_with_seed` derived address it will announce) via `system_instruction::create_account(..., &P, lamports, space, &owner)`.
2. Attacker, observing `P` (from a pending transaction in gossip/mempool, or by independently deriving the seed-based address before A's transaction lands), submits an ordinary `system_instruction::transfer(attacker, P, 1)`.
3. `transfer_verified` (`programs/system/src/system_processor.rs:216-243`) succeeds unconditionally — it only validates the sender, not the recipient — so `P` now holds 1 lamport.
4. A's subsequent `create_account` transaction now hits `if to.get_lamports() > 0 { return Err(SystemError::AccountAlreadyInUse.into()); }` at `programs/system/src/system_processor.rs:164-171` and reverts every time, indefinitely blocking initialization of `P`.

### Citations

**File:** programs/system/src/system_processor.rs (L160-182)
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
    transfer(
        from_account_index,
        to_account_index,
        lamports,
        invoke_context,
        instruction_context,
    )
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

**File:** programs/system/src/system_processor.rs (L330-378)
```rust
        SystemInstruction::CreateAccount {
            lamports,
            space,
            owner,
        } => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let to_address = Address::create(
                instruction_context.get_key_of_instruction_account(1)?,
                None,
                invoke_context,
            )?;
            create_account(
                0,
                1,
                &to_address,
                lamports,
                space,
                &owner,
                &signers,
                invoke_context,
                &instruction_context,
            )
        }

        SystemInstruction::CreateAccountWithSeed {
            base,
            seed,
            lamports,
            space,
            owner,
        } => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let to_address = Address::create(
                instruction_context.get_key_of_instruction_account(1)?,
                Some((&base, &seed, &owner)),
                invoke_context,
            )?;
            create_account(
                0,
                1,
                &to_address,
                lamports,
                space,
                &owner,
                &signers,
                invoke_context,
                &instruction_context,
            )
        }
```

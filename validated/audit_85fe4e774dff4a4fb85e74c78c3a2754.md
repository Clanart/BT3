### Title
`SystemInstruction::CreateAccount` griefing via unsolicited lamport transfer permanently DoSes account initialization for a targeted address - (File: `programs/system/src/system_processor.rs`)

### Summary
The `CNote` bug relies on an on-chain invariant ("underlying balance must be exactly 0 before certain operations") that any unprivileged actor can break by directly transferring tokens to the contract, permanently bricking functions that assert `getCashPrior() == 0`. The System Program's `create_account` builtin instruction handler has the structurally identical invariant: it requires the destination account's lamport balance to be exactly `0` (`to.get_lamports() > 0` ⇒ reject) before allowing account creation/initialization. Because any unprivileged party can send lamports to an arbitrary pubkey via `SystemInstruction::Transfer` before the target keypair/PDA is ever used, this "must be zero" precondition can always be broken in advance, permanently preventing the legitimate owner from creating (initializing) the account at that address.

### Finding Description
`create_account` in `system_processor.rs` checks the `to` account's lamport balance and refuses to proceed if it is nonzero: [1](#0-0) 

```
// if it looks like the `to` account is already in use, bail
{
    let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
    if to.get_lamports() > 0 {
        ic_msg!(... "Create Account: account {:?} already in use" ...);
        return Err(SystemError::AccountAlreadyInUse.into());
    }
    allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
}
```

This mirrors `CNote`'s `getCashPrior() == 0` guard: the code assumes that if the balance is nonzero the account must already be "in use" and legitimately owned/initialized by someone else, so it refuses to touch it. But on Solana, **any account address can receive lamports from anyone** via a plain `SystemInstruction::Transfer` (`transfer_verified`/`transfer` in the same file) with zero permission over the destination — the destination does not need to sign or be owned by any particular program: [2](#0-1) 

Because lamports can be pushed to any pubkey unconditionally, an attacker who front-loads even 1 lamport to a not-yet-created address permanently sets `to.get_lamports() > 0` for that address. Any subsequent legitimate `CreateAccount` targeting that exact address (used e.g. to create a new stake account, vote account, nonce account, or any PDA/keypair account via the system program) will unconditionally fail with `SystemError::AccountAlreadyInUse`, exactly as CNote's functions revert once `getCashPrior() != 0`. `CreateAccountWithSeed` funnels through the same `create_account` function and inherits the identical flaw: [3](#0-2) 

The guard's intent ("if it looks like the `to` account is already in use, bail") is defeated by the fact that lamport balance alone is not a reliable signal of "in use" — it conflates "has nonzero balance" with "has been initialized/owned," just as CNote conflated "nonzero underlying balance" with "mid-operation state."

### Impact Explanation
This is an unprivileged, permanent denial-of-service against any specific target address chosen by the attacker before it is created: legitimate `CreateAccount`/`CreateAccountWithSeed` transactions targeting that address will deterministically fail forever (the balance can never be reset back to zero without withdrawing/burning, which the intended owner cannot do since they don't control the keypair authority at that point and the funds are typically not withdrawable without the account already being initialized). This directly matches the "built-ins" category of valid impact (unprivileged issue in a built-in program causing consistent transaction failure / false rejection of legitimate execution), and can be weaponized to block specific validators/users from creating stake, vote, or nonce accounts at addresses they intend to use, or to block deterministic PDA-style account creation flows that rely on `CreateAccount`.

### Likelihood Explanation
High likelihood of triggerability: the attacker only needs to know the target's public key in advance (which is often derivable/predictable — e.g., addresses announced before account creation, addresses derived deterministically off-chain, or observed in a not-yet-landed transaction in the mempool/gossip) and send a single, cheap `SystemInstruction::Transfer` of 1 lamport. No privileged role, no malicious validator assumption, and no race condition beyond simple front-running of a public address is required.

### Recommendation
Do not use raw lamport balance as the sole "already initialized" signal in `create_account`. Instead:
- Check `account.get_data().is_empty() && system_program::check_id(account.get_owner())` (as `allocate` already does) as the primary "in use" signal, and only require the pre-existing lamports be topped up to the requested `lamports` rather than rejecting outright when balance is nonzero (mirroring the "allow prefund" pattern already implemented in `create_account_allow_prefund`, see `programs/system/src/system_processor.rs` lines 184-214).
- More broadly, apply the `create_account_allow_prefund` semantics uniformly to `CreateAccount`/`CreateAccountWithSeed` so pre-funded (griefed) accounts can still be initialized as long as they are unassigned/empty, closing the exact class of bug described in the CNote report (relying on a forceable balance value as an exact-match invariant).

### Proof of Concept
1. Off-chain, the victim generates (but has not yet used) a keypair `T` intended to become a new stake/vote/nonce account, or a deterministic PDA/seeded address is publicly known ahead of use.
2. Attacker submits `SystemInstruction::Transfer { lamports: 1 }` from any funded account to `T`, which succeeds unconditionally via `transfer`/`transfer_verified` (`programs/system/src/system_processor.rs` lines 216-268) since `T` requires no signature or ownership to receive funds.
3. Victim submits `SystemInstruction::CreateAccount { lamports, space, owner }` with `to = T`.
4. In `create_account`, `to.get_lamports() > 0` is now `true` (equals `1`), so the instruction returns `SystemError::AccountAlreadyInUse` (`programs/system/src/system_processor.rs` lines 164-171), and the victim can never initialize `T` via `CreateAccount`, permanently denying them the intended account address.

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

**File:** programs/system/src/system_processor.rs (L216-243)
```rust
fn transfer_verified(
    from_account_index: IndexOfAccount,
    to_account_index: IndexOfAccount,
    lamports: u64,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    let mut from = instruction_context.try_borrow_instruction_account(from_account_index)?;
    if !from.get_data().is_empty() {
        ic_msg!(invoke_context, "Transfer: `from` must not carry data");
        return Err(InstructionError::InvalidArgument);
    }
    if lamports > from.get_lamports() {
        ic_msg!(
            invoke_context,
            "Transfer: insufficient lamports {}, need {}",
            from.get_lamports(),
            lamports
        );
        return Err(SystemError::ResultWithNegativeLamports.into());
    }

    from.checked_sub_lamports(lamports)?;
    drop(from);
    let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
    to.checked_add_lamports(lamports)?;
    Ok(())
}
```

**File:** programs/system/src/system_processor.rs (L354-378)
```rust
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

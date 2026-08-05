This is exactly the analog: `create_account` (`programs/system/src/system_processor.rs:150-182`) at line 164 rejects the entire `CreateAccount` instruction with `SystemError::AccountAlreadyInUse` if the target address already has `lamports() > 0`, regardless of who put the lamports there. [1](#0-0) 

### Title
Unprivileged lamport-donation griefing permanently blocks `CreateAccount` for PDAs/derived addresses — (File: programs/system/src/system_processor.rs)

### Summary
Any unprivileged party can send SOL via a plain `system_instruction::transfer` to any pubkey, including a PDA or not-yet-created address that a victim program/user intends to initialize later with `SystemInstruction::CreateAccount`. Because `create_account` in the System program treats *any* non-zero lamport balance as "already in use," the victim's subsequent `CreateAccount` call unconditionally fails, exactly mirroring the Morpho/Aave report's pattern of an attacker unilaterally donating an asset to force a victim contract into a state that its normal "create/initialize" logic was never designed to tolerate, and which existing guards do not distinguish from a legitimately-initialized account.

### Finding Description
`create_account` in `system_processor.rs` performs this check before allocating space and assigning the owner: [2](#0-1) 
```
if to.get_lamports() > 0 {
    ic_msg!(..., "Create Account: account {:?} already in use", to_address);
    return Err(SystemError::AccountAlreadyInUse.into());
}
```
This check only inspects `lamports() > 0` — it does not check `data.len() > 0` or `owner != system_program::id()` (those richer checks exist in the separate `allocate` path used for `Allocate`/`CreateAccountWithSeed` semantics, but the actual `lamports()>0` gate in `create_account` fires unconditionally). Since any account, whether a fresh keypair address or a program-derived address (PDA), is a valid System-program-owned, empty-data account by default, and since `system_instruction::transfer` requires no permission from the recipient (the "to" account is never required to sign a transfer — see `transfer_verified`/`transfer`), an attacker can pre-fund the target address with 1 lamport before the legitimate creator's transaction lands. [3](#0-2) 

This is the "attacker sends an unrequested asset to a victim address, and the victim's normal operations subsequently break" broken invariant that the Morpho report describes for AToken LTV=0 poisoning: the target believed to be "no balance / not yet initialized" is unilaterally corrupted by a third party, and the code path responsible for the victim's legitimate operation (`CreateAccount` here, `withdraw`/`borrow`/`liquidate` in Morpho) has no mechanism to distinguish "poisoned by attacker" from "already legitimately in use," so it always refuses to proceed.

Unlike a normal fee-payer/keypair account (where the owner controls whether to attempt account creation and could choose a fresh unused address), PDAs are the more consequential case: their address is deterministic and publicly derivable from `Pubkey::find_program_address`, so an attacker can precompute the exact PDA a program will try to `create_account` for (e.g., an escrow, vault, or ATA-like account) and pre-fund it with a single lamport before the transaction that creates it is processed. There is no signer requirement or authorization check on the sender of the griefing transfer.

### Impact Explanation
This falls under the **runtime/accounts and built-ins** category: an unprivileged attacker can permanently and repeatedly (each time the victim retries with the same derived address) block any protocol relying on System-program `CreateAccount` for a deterministic address (PDAs, seed-derived accounts) from initializing that account, causing denial-of-service for the specific account/derived-address until the caller switches strategies (e.g., using `create_account_allow_prefund`, which is a program/runtime-internal helper not generally exposed to arbitrary CPI callers) or manually reclaims/transfers the donated lamports (which nothing in the System program permits without owning/signing for the account). Because `create_account` is the standard way on-chain programs create PDA-owned state accounts, this griefing primitive can disrupt legitimate initialization flows for any protocol that derives account addresses deterministically and expects them to start at zero lamports — a denial-of-service on normal execution/acceptance of otherwise-valid transactions, consistent with valid-impact criteria (non-RPC remote low-cost DoS on transaction execution/acceptance).

### Likelihood Explanation
High. The attack requires only a single, cheap, permissionless `system_instruction::transfer` of 1 lamport to a publicly-computable address (any PDA derivable via `find_program_address`, or any address whose creation is anticipated). No signature from the target account, no special privileges, and no race with the specific creating transaction is even needed — the attacker can pre-fund the address at any point before the victim's `CreateAccount` transaction executes.

### Recommendation
`create_account`'s "already in use" check should not treat unsolicited lamport donations to a still system-owned, empty-data account as "in use." At minimum, mirror the richer check used in `allocate` (data non-empty OR owner not system program) rather than gating purely on `lamports() > 0`, and top up the account to the requested `lamports` value via `checked_add_lamports`/`transfer` semantics that account for a pre-existing balance (similar to the internal `create_account_allow_prefund` helper), rather than unconditionally rejecting.

### Proof of Concept
1. Program `P` derives PDA `vault = find_program_address([...], P)`.
2. Attacker submits `system_instruction::transfer(attacker, vault, 1)` — succeeds unconditionally; `transfer_verified` requires no signature or permission from `vault`. [3](#0-2) 
3. User calls program `P`'s "initialize vault" instruction, which CPIs `system_instruction::create_account(payer, vault, lamports, space, P)`.
4. In `create_account`, `to.get_lamports() > 0` (now 1) → returns `SystemError::AccountAlreadyInUse`, aborting the CPI and the whole transaction. [2](#0-1) 
5. Every retry by the legitimate user with the same derived address fails identically; the vault can never be created through the normal `CreateAccount` path, denying service to the protocol at that deterministic address.

**Uncertainty note:** I was unable to fully verify whether higher-level protocol code elsewhere in this repo (or in downstream consumers) already routes around this via `create_account_allow_prefund` or an equivalent "top-up" pattern for all PDA-creation call sites; the index only surfaces this helper as available in `system_processor.rs` itself, and I could not confirm how widely it is used versus the plain `CreateAccount` instruction across the codebase's built-in programs (stake/vote/config) or CPI callers.

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

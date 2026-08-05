### Title
`create_account()` presence check allows unprivileged lamport pre-funding to permanently block legitimate account creation and grief transaction fees - (`programs/system/src/system_processor.rs`)

### Summary
The external report describes `ExecutionEnvironment.solverMetaTryCatch()` assuming `address(this).balance == solverOp.value`, an invariant that anyone can break by sending unsolicited ETH to the contract beforehand, forcing the legitimate call to revert and making the solver pay wasted gas. The Agave analog is the System Program's `create_account()`, which assumes the target account has zero lamports before creation and unconditionally rejects the operation if that assumption is violated — an invariant any unprivileged actor can break simply by transferring lamports to the target address ahead of time.

### Finding Description
`create_account()` in `programs/system/src/system_processor.rs` enforces:

```rust
let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
if to.get_lamports() > 0 {
    ic_msg!(invoke_context, "Create Account: account {:?} already in use", to_address);
    return Err(SystemError::AccountAlreadyInUse.into());
}
``` [1](#0-0) 

This assumes the destination account is untouched (0 lamports) at the moment the `CreateAccount`/`CreateAccountWithSeed` instruction executes. However, the System Program allows *anyone* to add lamports to *any* pubkey via a plain `Transfer` instruction, with no requirement that the recipient sign or even exist yet — the corresponding `transfer_verified()`/`transfer()` helpers only require the *sender* to sign, and place no restriction on the destination account's owner or prior state [2](#0-1) . Because the destination address for `CreateAccount` (or a `CreateAccountWithSeed` derived address, which is fully deterministic from `base`/`seed`/`owner`) is public and predictable, an attacker can pre-fund it with a single lamport at any time — not necessarily by racing a specific in-flight transaction, but simply by squatting on any address they anticipate will later be initialized (e.g., a nonce account, a deterministically seeded PDA-like account, or any vanity/derived address a wallet/program is about to create). When the legitimate creation transaction later executes, `to.get_lamports() > 0` is true, so the instruction unconditionally returns `SystemError::AccountAlreadyInUse`, and the transaction fails — yet the transaction's fee payer is still charged the base fee, since Solana charges signature fees on inclusion regardless of instruction execution outcome.

Agave engineers have already recognized this exact defect class: a new, feature-gated instruction `CreateAccountAllowPrefund` / `create_account_allow_prefund()` was added specifically to let creation succeed "where account has already had rent paid in whole or in part before creation" [3](#0-2) , and it is exposed via `SystemInstruction::CreateAccountAllowPrefund` gated behind `invoke_context.get_feature_set().create_account_allow_prefund` [4](#0-3) . This confirms the pre-funding griefing scenario is a known, real issue — but the fix is opt-in per-instruction. The original, widely used `CreateAccount`/`CreateAccountWithSeed` paths (used pervasively by wallets, the stake/vote/nonce account creation flows, and countless dApps) retain the brittle equality-style `> 0` presence check and remain unprotected.

### Impact Explanation
Any unprivileged party can permanently block a specific, predictable target address from ever being initialized via the standard `CreateAccount` path by pre-funding it with a single lamport, and can repeat this against any number of addresses at negligible cost. Every legitimate attempt to create that account fails with `AccountAlreadyInUse`, while the fee payer is still charged the transaction fee for the failed transaction — a direct, repeatable fund-loss/griefing vector against unprivileged users, and a denial-of-service against dApps/wallets that rely on deterministic account creation (nonce accounts, seed-derived accounts, etc.).

### Likelihood Explanation
The primitive required is trivial and fully unprivileged: a `System::Transfer` of 1 lamport to any known/derivable pubkey, requiring only a valid keypair with minimal SOL. No node compromise, no validator collusion, and no precise transaction-ordering/front-running is required — the attacker can pre-fund addresses well in advance of any specific transaction, since `CreateAccountWithSeed` destinations are deterministic and publicly derivable, and regular `CreateAccount` destinations are often known ahead of time (e.g., published nonce/stake account addresses). The existence of the dedicated `create_account_allow_prefund` mitigation confirms this is a recognized, exploitable pattern, currently unresolved for the default creation path.

### Recommendation
Extend the `create_account_allow_prefund` semantics (or an equivalent lamport-tolerant check) to the default `CreateAccount`/`CreateAccountWithSeed` instruction handling, rather than requiring callers to opt into a separate, feature-gated instruction. At minimum, distinguish "already initialized with data/owner" (a genuine conflict) from "merely holds lamports" (an innocuous pre-funding) so that legitimate creation cannot be permanently DoS'd by an unprivileged lamport transfer.

### Proof of Concept
1. Attacker observes/derives a target pubkey `T` that a victim (wallet, dApp, or validator tooling) intends to use for `CreateAccount` or `CreateAccountWithSeed` (e.g., a nonce account address computed via `Pubkey::create_with_seed`).
2. Attacker submits `SystemInstruction::Transfer` of 1 lamport from attacker's own funded account to `T`, at any point before the victim's creation transaction lands.
3. Victim submits `SystemInstruction::CreateAccount { lamports, space, owner }` targeting `T`.
4. In `create_account()`, `to.get_lamports() > 0` evaluates true, and the instruction returns `SystemError::AccountAlreadyInUse` [5](#0-4) ; the victim's transaction fails but the victim's fee payer is still charged the transaction fee, and `T` can never be initialized via `CreateAccount` again unless the attacker's 1 lamport is somehow drained first (which the victim cannot do without owning/controlling `T`, since it has no program owner).

### Citations

**File:** programs/system/src/system_processor.rs (L161-171)
```rust
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

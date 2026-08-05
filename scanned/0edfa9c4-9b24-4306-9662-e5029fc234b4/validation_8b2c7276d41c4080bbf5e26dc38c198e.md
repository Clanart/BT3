### Title
Unprivileged lamport-griefing of `SystemInstruction::CreateAccount` blocks account initialization ("Vow-flop" analog in the System Program) - (File: `programs/system/src/system_processor.rs`)

### Summary
The Multi-Collateral Dai `Vow.flop()` bug required `vat.dai(address(this)) == 0`, and any unprivileged account could grief that precondition by pushing a trivial amount of Dai into the Vow via the permissionless `Vat.move`. The exact same pattern exists in Agave's System Program: `create_account` requires the destination account's lamport balance to be exactly zero, and any unprivileged party can push lamports into that destination via the permissionless `system_processor::transfer`/`transfer_verified` path (no signature from the recipient required), permanently failing every future `CreateAccount` attempt at that address.

### Finding Description
`create_account` in `programs/system/src/system_processor.rs` enforces a strict "balance must be zero" precondition before allowing account creation: [1](#0-0) 

```
    // if it looks like the `to` account is already in use, bail
    {
        let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
        if to.get_lamports() > 0 {
            ...
            return Err(SystemError::AccountAlreadyInUse.into());
        }
        allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
    }
```

This is invoked directly by `SystemInstruction::CreateAccount`/`CreateAccountWithSeed` dispatch [2](#0-1) , and is confirmed by the unit test `test_create_already_in_use`, which explicitly shows that pre-existing lamports (`AccountSharedData::new(1, 0, &Pubkey::default())`) cause `AccountAlreadyInUse` even though the account has no data and no owner conflict [3](#0-2) .

Meanwhile, `transfer`/`transfer_verified` only requires a signature from the *sender* (`from`), never from the *recipient* (`to`) [4](#0-3) . Anyone can therefore call `SystemInstruction::Transfer` sending 1 lamport to any target pubkey, at any time, with no cooperation or consent from the owner of that pubkey — exactly analogous to `Vat.move` in the ToB report, which anyone could call to push Dai into `Vow`.

This means any address that a protocol, wallet, or program intends to `CreateAccount` at (a deterministic or otherwise publicly-known target pubkey, e.g. a not-yet-created PDA-derived token/state account, a vanity/derived keypair address, or an address whose creation transaction is publicly visible before landing) can be permanently "poisoned" by anyone sending it a single lamport first. The legitimate `CreateAccount` transaction will then fail with `AccountAlreadyInUse` for as long as the balance remains non-zero, blocking initialization of that account.

Existing guards do not stop this path: `create_account` has no mechanism to "heal" (drain/absorb) unexpected lamports the way MakerDAO's fix direction suggested; it strictly rejects any non-zero balance regardless of who put it there or why. The engineering team's own fix for this exact problem is `create_account_allow_prefund` / `CreateAccountAllowPrefund`, which explicitly bypasses the zero-balance check [5](#0-4)  and is gated behind the `create_account_allow_prefund` feature flag [6](#0-5) . However, this mitigation only helps callers who explicitly use the new `CreateAccountAllowPrefund` instruction; the original, still ubiquitously used `CreateAccount`/`CreateAccountWithSeed` instructions remain fully exposed to this griefing pattern.

### Impact Explanation
This is a permissionless, non-consensus-breaking denial-of-service primitive against account initialization: any unprivileged attacker can prevent a specific target account from ever being created via the standard `CreateAccount` path by pre-funding it with 1 lamport, causing repeated transaction failures (`AccountAlreadyInUse`) for legitimate users/programs attempting to set up that account, and wasting their transaction fees. This maps to the "unprivileged ... transactions ... that cause ... false execution/acceptance" and DoS categories, since it forces rejection of otherwise-valid account-creation transactions without any special privilege, front-running of a mempool (Solana has none), or cooperation from a malicious validator — the attack only requires submitting an ordinary `Transfer` instruction before the target `CreateAccount` transaction lands.

### Likelihood Explanation
High. The griefing primitive (`Transfer` to an arbitrary unsigned recipient) is a completely standard, always-available System Program instruction with no special conditions. The only requirement is that the attacker know the target address ahead of time, which is the normal case for deterministic/derived addresses or any publicly observed pending transaction. No race against a specific block/leader is strictly required — the attacker just needs to land the 1-lamport transfer before the victim's `CreateAccount` transaction, which is straightforward given the account only needs to be poisoned once, persistently.

### Recommendation
- For newly written/adopted protocols, prefer `CreateAccountAllowPrefund` (`create_account_allow_prefund`), which already removes the zero-balance precondition and correctly folds any pre-existing lamports into the funding transfer instead of failing.
- For the legacy `CreateAccount`/`CreateAccountWithSeed` paths, consider making the "already in use" check based on the account actually being allocated (`space > 0`/owner already set to something other than the System Program) rather than strictly on lamports, since lamports alone convey no information about whether the account is "in use" from a state perspective — mirroring the Trail of Bits recommendation to relax an overly strict zero-balance invariant that an unprivileged party can trivially violate.
- Document explicitly, in SDK helper functions (e.g. `system_instruction::create_account`), that any address a caller intends to initialize should be checked/topped-up with `CreateAccountAllowPrefund` or should tolerate pre-existing dust to avoid this DoS.

### Proof of Concept
1. Attacker observes (or predicts) that Victim intends to submit a transaction containing `SystemInstruction::CreateAccount { lamports, space, owner }` targeting `to_address` (e.g., a deterministic/derived account not yet created).
2. Attacker submits `SystemInstruction::Transfer { lamports: 1 }` from any funded account of theirs to `to_address`. This only requires the attacker's own signature [7](#0-6) ; no cooperation from Victim or the owner of `to_address` is needed.
3. Once this transaction lands, `to_address` now has `lamports = 1`.
4. Victim's subsequent `CreateAccount` transaction executes `create_account`, hits `to.get_lamports() > 0`, and fails with `SystemError::AccountAlreadyInUse` [8](#0-7) , exactly as demonstrated by the existing test case `test_create_already_in_use` (third scenario: "Attempt to create an account that already has lamports") [3](#0-2) .
5. Victim's account can never be created via `CreateAccount` at that address as long as the balance remains non-zero, and the attacker can repeat step 2 indefinitely at negligible cost to keep the account permanently poisoned.

### Citations

**File:** programs/system/src/system_processor.rs (L117-182)
```rust
fn assign(
    account: &mut BorrowedInstructionAccount,
    address: &Address,
    owner: &Pubkey,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
) -> Result<(), InstructionError> {
    // no work to do, just return
    if account.get_owner() == owner {
        return Ok(());
    }

    if !address.is_signer(signers) {
        ic_msg!(invoke_context, "Assign: account {:?} must sign", address);
        return Err(InstructionError::MissingRequiredSignature);
    }

    account.set_owner(&owner.to_bytes())
}

fn allocate_and_assign(
    to: &mut BorrowedInstructionAccount,
    to_address: &Address,
    space: u64,
    owner: &Pubkey,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
) -> Result<(), InstructionError> {
    allocate(to, to_address, space, signers, invoke_context)?;
    assign(to, to_address, owner, signers, invoke_context)
}

#[allow(clippy::too_many_arguments)]
fn create_account(
    from_account_index: IndexOfAccount,
    to_account_index: IndexOfAccount,
    to_address: &Address,
    lamports: u64,
    space: u64,
    owner: &Pubkey,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
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

**File:** programs/system/src/system_processor.rs (L216-268)
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

fn transfer(
    from_account_index: IndexOfAccount,
    to_account_index: IndexOfAccount,
    lamports: u64,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    if !instruction_context.is_instruction_account_signer(from_account_index)? {
        ic_msg!(
            invoke_context,
            "Transfer: `from` account {} must sign",
            instruction_context.get_key_of_instruction_account(from_account_index)?,
        );
        return Err(InstructionError::MissingRequiredSignature);
    }

    transfer_verified(
        from_account_index,
        to_account_index,
        lamports,
        invoke_context,
        instruction_context,
    )
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

**File:** programs/system/src/system_processor.rs (L1014-1041)
```rust
        // Attempt to create an account that already has lamports
        let owned_account = AccountSharedData::new(1, 0, &Pubkey::default());
        let unchanged_account = owned_account.clone();
        let accounts = process_instruction(
            &bincode::serialize(&SystemInstruction::CreateAccount {
                lamports: 50,
                space: 2,
                owner: new_owner,
            })
            .unwrap(),
            vec![(from, from_account), (owned_key, owned_account)],
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
    }
```

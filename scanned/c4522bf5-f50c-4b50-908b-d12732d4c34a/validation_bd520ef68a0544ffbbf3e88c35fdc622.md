## Title
Front-runnable, permissionless `InitializeNonceAccount` permanently locks a victim's nonce account with an attacker-chosen authority - (File: `programs/system/src/system_instruction.rs`)

### Summary
The Vader `vestFor` bug allowed anyone to call an unauthenticated function that permanently sets state (a 365-day vesting lock) on behalf of another user, with no re-entry/override path, enabling front-running griefing. The direct Agave analog is `initialize_nonce_account` in the System Program: any signer can submit `SystemInstruction::InitializeNonceAccount` against *any* writable, system-owned, uninitialized nonce account and it does not require the nonce account itself (or its intended owner) to be a signer — only that it is writable and currently `State::Uninitialized`. Once initialized, the `authority` field is permanently set from whatever pubkey the caller supplied, and the account cannot be re-initialized (`State::Initialized(_) => Err(InvalidAccountData)`), only withdrawn/closed by the (now attacker-controlled) authority.

### Finding Description
`initialize_nonce_account` only checks that the account is writable and currently `Uninitialized`; it never checks that the caller (or the nonce account) actually signed the transaction with the intended owner's key, and it accepts an arbitrary `nonce_authority: &Pubkey` argument supplied by whoever submits the instruction: [1](#0-0) 

The normal, expected flow is: (1) `CreateAccount` — which does require the new account to sign — to allocate/assign a fresh account to the System Program, followed by (2) a separate `InitializeNonceAccount` instruction (often in the same transaction, but it is a distinct, independently invocable instruction) that sets the durable-nonce state and authority. Because these are two independent instructions, and because `initialize_nonce_account` has no signer/authority check tying the caller to the account owner, an attacker who observes a pending/likely `CreateAccount` for a nonce account (e.g., via mempool/gossip visibility of an unconfirmed tx, or simply races a known deterministic nonce-account address such as a PDA-seeded one) can submit their own `InitializeNonceAccount` instruction against that same pubkey the moment it becomes system-owned with zero-filled data, setting `nonce_authority` to an address they control.

Once this succeeds, the account transitions to `State::Initialized(data)` with `data.authority` = attacker's key. Any subsequent legitimate `InitializeNonceAccount` call from the real owner will hit the `State::Initialized(_)` branch and fail with `InstructionError::InvalidAccountData` — there is no way to "re-initialize" or override it: [2](#0-1) 

The only recovery path is `withdraw_nonce_account`, but that requires a signature from `data.authority` — i.e., the attacker — for anything less than a full lamport withdrawal, or for advancing/withdrawing under the "insufficient lamports" and "authority signature" checks: [3](#0-2) 

This is structurally identical to `vestFor`: an unauthenticated, front-runnable call that installs a durable, attacker-chosen piece of state on behalf of a victim's account, with no whitelist/authorization check restricting who may call it and no legitimate override once installed.

### Impact Explanation
A victim's intended nonce account becomes permanently unusable for durable-nonce transactions (their advance/withdraw/authorize calls will require the attacker's signature, which will never be given), effectively bricking the account for its intended purpose (fee payer replacement, offline signing, etc.) and any lamports funded into it are only withdrawable by the attacker-installed authority. This is a fund-loss/loss-of-availability primitive against unprivileged users, matching the "fund theft/loss" and "false execution/acceptance" categories for the runtime/accounts path, without requiring any malicious validator, admin, or trusted-integration assumption — only an ordinary front-running attacker submitting an ordinary transaction.

### Likelihood Explanation
Requires the attacker to observe (via normal transaction propagation/QUIC-TPU ingestion or gossip of unconfirmed transactions) that a target account is about to be, or has just been, allocated to the system program with zero data (post-`CreateAccount`, pre-`InitializeNonceAccount`), and to win a race to land their own `InitializeNonceAccount` instruction first. This is feasible whenever nonce-account creation and initialization are not atomic within a single transaction (which is not enforced anywhere in the instruction processing) or whenever the target address is predictable in advance (e.g., derived deterministically). No special validator privilege is needed — this is a standard front-run against public mempool visibility.

### Recommendation
Require that `initialize_nonce_account` verify that the caller who is establishing the authority is the same signer who funded/created the account (e.g., require the nonce account itself, or a designated creator key, to be a signer of the `InitializeNonceAccount` instruction), or document/enforce that `CreateAccount` + `InitializeNonceAccount` must occur atomically in a single transaction so no third party can interleave an instruction against the intermediate state. At minimum, add a signer check on the account being initialized (mirroring the `allocate`/`assign` pattern in `system_processor.rs`, which does require `address.is_signer(signers)`): [4](#0-3) 

### Proof of Concept
1. Victim submits `SystemInstruction::CreateAccount` allocating and assigning `nonce_pubkey` to the System Program (signed by `nonce_pubkey`), intending to follow up with `InitializeNonceAccount`.
2. Attacker, observing the pending/landed `CreateAccount` transaction (before the victim's `InitializeNonceAccount` lands, or in the gap between two separate transactions), submits their own transaction: `SystemInstruction::InitializeNonceAccount(attacker_authority)` with `nonce_pubkey` as the writable target account.
3. `initialize_nonce_account` sees `State::Uninitialized`, checks only `is_writable()` and minimum balance, and calls `account.set_state(&Versions::new(State::Initialized(data)))` with `data.authority == attacker_authority`: [5](#0-4) 
4. Victim's subsequent `InitializeNonceAccount` (or any use expecting themselves as authority) now hits `State::Initialized(_) => Err(InstructionError::InvalidAccountData)` and permanently fails, while all nonce operations (`advance`, `withdraw`, `authorize`) require `attacker_authority`'s signature per the checks in `withdraw_nonce_account`/`authorize_nonce_account`: [6](#0-5)

### Citations

**File:** programs/system/src/system_instruction.rs (L80-153)
```rust
pub(crate) fn withdraw_nonce_account(
    from_account_index: IndexOfAccount,
    lamports: u64,
    to_account_index: IndexOfAccount,
    rent: &Rent,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    let mut from = instruction_context.try_borrow_instruction_account(from_account_index)?;
    if !from.is_writable() {
        ic_msg!(
            invoke_context,
            "Withdraw nonce account: Account {} must be writeable",
            from.get_key()
        );
        return Err(InstructionError::InvalidArgument);
    }

    let check_signer = |signer: &Pubkey| {
        if !signers.contains(signer) {
            ic_msg!(
                invoke_context,
                "Withdraw nonce account: Account {} must sign",
                signer
            );
            return Err(InstructionError::MissingRequiredSignature);
        }
        Ok(())
    };

    let state: Versions = from.get_state()?;
    match state.state() {
        State::Uninitialized => {
            if lamports > from.get_lamports() {
                ic_msg!(
                    invoke_context,
                    "Withdraw nonce account: insufficient lamports {}, need {}",
                    from.get_lamports(),
                    lamports,
                );
                return Err(InstructionError::InsufficientFunds);
            }
            check_signer(from.get_key())?;
        }
        State::Initialized(data) => {
            if lamports == from.get_lamports() {
                let durable_nonce =
                    DurableNonce::from_blockhash(&invoke_context.environment_config.blockhash);
                if data.durable_nonce == durable_nonce {
                    ic_msg!(
                        invoke_context,
                        "Withdraw nonce account: nonce can only advance once per slot"
                    );
                    return Err(SystemError::NonceBlockhashNotExpired.into());
                }
                check_signer(&data.authority)?;
                from.set_state(&Versions::new(State::Uninitialized))?;
            } else {
                let min_balance = rent.minimum_balance(from.get_data().len());
                let amount = checked_add(lamports, min_balance)?;
                if amount > from.get_lamports() {
                    ic_msg!(
                        invoke_context,
                        "Withdraw nonce account: insufficient lamports {}, need {}",
                        from.get_lamports(),
                        amount,
                    );
                    return Err(InstructionError::InsufficientFunds);
                }
                check_signer(&data.authority)?;
            }
        }
    };
```

**File:** programs/system/src/system_instruction.rs (L163-211)
```rust
pub(crate) fn initialize_nonce_account(
    account: &mut BorrowedInstructionAccount,
    nonce_authority: &Pubkey,
    rent: &Rent,
    invoke_context: &InvokeContext,
) -> Result<(), InstructionError> {
    if !account.is_writable() {
        ic_msg!(
            invoke_context,
            "Initialize nonce account: Account {} must be writeable",
            account.get_key()
        );
        return Err(InstructionError::InvalidArgument);
    }

    match account.get_state::<Versions>()?.state() {
        State::Uninitialized => {
            let min_balance = rent.minimum_balance(account.get_data().len());
            if account.get_lamports() < min_balance {
                ic_msg!(
                    invoke_context,
                    "Initialize nonce account: insufficient lamports {}, need {}",
                    account.get_lamports(),
                    min_balance
                );
                return Err(InstructionError::InsufficientFunds);
            }
            let durable_nonce =
                DurableNonce::from_blockhash(&invoke_context.environment_config.blockhash);
            let data = nonce::state::Data::new(
                *nonce_authority,
                durable_nonce,
                invoke_context
                    .environment_config
                    .blockhash_lamports_per_signature,
            );
            let state = State::Initialized(data);
            account.set_state(&Versions::new(state))
        }
        State::Initialized(_) => {
            ic_msg!(
                invoke_context,
                "Initialize nonce account: Account {} state is invalid",
                account.get_key()
            );
            Err(InstructionError::InvalidAccountData)
        }
    }
}
```

**File:** programs/system/src/system_instruction.rs (L213-249)
```rust
pub(crate) fn authorize_nonce_account(
    account: &mut BorrowedInstructionAccount,
    nonce_authority: &Pubkey,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
) -> Result<(), InstructionError> {
    if !account.is_writable() {
        ic_msg!(
            invoke_context,
            "Authorize nonce account: Account {} must be writeable",
            account.get_key()
        );
        return Err(InstructionError::InvalidArgument);
    }
    match account
        .get_state::<Versions>()?
        .authorize(signers, *nonce_authority)
    {
        Ok(versions) => account.set_state(&versions),
        Err(AuthorizeNonceError::Uninitialized) => {
            ic_msg!(
                invoke_context,
                "Authorize nonce account: Account {} state is invalid",
                account.get_key()
            );
            Err(InstructionError::InvalidAccountData)
        }
        Err(AuthorizeNonceError::MissingRequiredSignature(account_authority)) => {
            ic_msg!(
                invoke_context,
                "Authorize nonce account: Account {} must sign",
                account_authority
            );
            Err(InstructionError::MissingRequiredSignature)
        }
    }
}
```

**File:** programs/system/src/system_processor.rs (L75-100)
```rust
fn allocate(
    account: &mut BorrowedInstructionAccount,
    address: &Address,
    space: u64,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
) -> Result<(), InstructionError> {
    if !address.is_signer(signers) {
        ic_msg!(
            invoke_context,
            "Allocate: 'to' account {:?} must sign",
            address
        );
        return Err(InstructionError::MissingRequiredSignature);
    }

    // if it looks like the `to` account is already in use, bail
    //   (note that the id check is also enforced by message_processor)
    if !account.get_data().is_empty() || !system_program::check_id(account.get_owner()) {
        ic_msg!(
            invoke_context,
            "Allocate: account {:?} already in use",
            address
        );
        return Err(SystemError::AccountAlreadyInUse.into());
    }
```

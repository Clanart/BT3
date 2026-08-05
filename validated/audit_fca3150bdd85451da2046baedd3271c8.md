Audit Report

## Title
`Close` on an `UpgradeableLoaderState::Uninitialized` account drains lamports with no signer/authority check - (File: programs/bpf_loader/src/lib.rs)

## Summary
The `UpgradeableLoaderInstruction::Close` handler branches on the loader state of the account being closed; the `Buffer` and `ProgramData` branches route through `common_close_account`, which enforces authority-match and signer checks, but the `Uninitialized` branch bypasses this entirely and unconditionally transfers the account's lamports to an attacker-specified recipient. Any account owned by `bpf_loader_upgradeable` that currently deserializes as `Uninitialized` and holds nonzero lamports can be drained by an unprivileged caller. [1](#0-0) 

## Finding Description
In `process_instruction`'s `Close` arm, the account at instruction index 0 is borrowed and its state is inspected via `close_account.get_state()`. When that state matches `UpgradeableLoaderState::Uninitialized`, the code immediately adds `close_account.get_lamports()` to the recipient account at index 1 and zeroes the source account's lamports — with no check that the recipient is authorized, no check that any signer participated, and no comparison against any stored authority: [2](#0-1) 

By contrast, the `Buffer` and `ProgramData` arms call `common_close_account`, which explicitly requires a non-`None` authority, requires that authority to match the signer at instruction index 2, and requires that account to have actually signed: [3](#0-2) 

The only backstop against an arbitrary caller draining an account it doesn't own is the transaction-context-level guard in `set_lamports`, which blocks a lamport decrease only when the account is *not* owned by the currently executing program: [4](#0-3)  Since the target account must already be owned by `bpf_loader_upgradeable` (the invoking program) for `get_state()`/`set_data_length()` to be meaningful in this code path, this generic ownership guard does not block the drain — no authority/signer check exists inside the loader itself for the `Uninitialized` case, unlike the `Buffer`/`ProgramData` cases.

This is a real, reachable precondition: `UpgradeableLoaderState::Uninitialized` legitimately occurs for accounts already assigned to `bpf_loader_upgradeable` (e.g., between a `CreateAccount`-style allocation/assignment and a subsequent `InitializeBuffer`, if these occur in separate transactions or if a client fails to complete the buffer-initialization step promptly) or after a prior `Buffer`/`ProgramData` closure, which explicitly resets state to `Uninitialized` via `common_close_account`'s `close_account.set_state(&UpgradeableLoaderState::Uninitialized)` [5](#0-4)  (though in that specific case lamports are already zeroed by the same call, so the exploitable window is for accounts funded and assigned to the loader but not yet initialized as a `Buffer`).

## Impact Explanation
This is a direct fund-theft vector: any unprivileged transaction sender can name an arbitrary recipient in instruction index 1 and drain the lamports of any writable, loader-owned account whose state is `Uninitialized`, with zero authorization required beyond naming the account. This corrupts the lamports balance of the victim account, transferring value to an attacker-chosen account with no consent from whoever funded/created it.

## Likelihood Explanation
The precondition — an account owned by `bpf_loader_upgradeable`, in `Uninitialized` state, holding nonzero lamports — arises naturally whenever account creation/assignment to the loader and buffer initialization are not atomic within a single transaction (e.g., a `CreateAccount` step succeeding while a subsequent `InitializeBuffer` has not yet executed). Any unprivileged user can then submit a single `Close` instruction with public inputs (the target account's public key and their own recipient address) to claim the funds; no signatures beyond the fee payer are required for the target or recipient account.

## Recommendation
Require explicit authorization in the `Uninitialized` branch as well, e.g., require the account being closed to sign for itself (mirroring how the System Program requires an account to authorize spending of its own lamports), or otherwise reject `Close` on `Uninitialized` accounts unless a verifiable owner/authority signature is present, instead of allowing an arbitrary caller to redirect the funds.

## Proof of Concept
1. Create account `A` via `system_instruction::create_account`, funding it with lamports and setting `owner = bpf_loader_upgradeable::id()` and `space = UpgradeableLoaderState::size_of_uninitialized()`, without following up (in the same transaction) with `InitializeBuffer`. `A`'s state deserializes as `UpgradeableLoaderState::Uninitialized`.
2. An attacker submits a `UpgradeableLoaderInstruction::Close` instruction with accounts `[A (writable, index 0), attacker_recipient (writable, index 1)]`.
3. In `process_instruction`, `close_account_state` matches `UpgradeableLoaderState::Uninitialized`; no signer/authority is checked; `attacker_recipient` receives all of `A`'s lamports per `programs/bpf_loader/src/lib.rs` lines 702-709.
4. Result: the attacker drains `A`'s lamports without ever holding any authority over `A`, confirmable via a Rust unit/integration test in `bpf_loader` tests that constructs such an account and asserts the transaction succeeds and lamports move to an unauthorized recipient.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L700-716)
```rust
            close_account.set_data_length(UpgradeableLoaderState::size_of_uninitialized())?;
            match close_account_state {
                UpgradeableLoaderState::Uninitialized => {
                    let mut recipient_account =
                        instruction_context.try_borrow_instruction_account(1)?;
                    recipient_account.checked_add_lamports(close_account.get_lamports())?;
                    close_account.set_lamports(0)?;

                    ic_logger_msg!(log_collector, "Closed Uninitialized {}", close_key);
                }
                UpgradeableLoaderState::Buffer { authority_address } => {
                    instruction_context.check_number_of_instruction_accounts(3)?;
                    drop(close_account);
                    common_close_account(&authority_address, &instruction_context, &log_collector)?;

                    ic_logger_msg!(log_collector, "Closed Buffer {}", close_key);
                }
```

**File:** programs/bpf_loader/src/lib.rs (L1003-1019)
```rust
fn common_close_account(
    authority_address: &Option<Pubkey>,
    instruction_context: &InstructionContext,
    log_collector: &Option<Rc<RefCell<LogCollector>>>,
) -> Result<(), InstructionError> {
    if authority_address.is_none() {
        ic_logger_msg!(log_collector, "Account is immutable");
        return Err(InstructionError::Immutable);
    }
    if *authority_address != Some(*instruction_context.get_key_of_instruction_account(2)?) {
        ic_logger_msg!(log_collector, "Incorrect authority provided");
        return Err(InstructionError::IncorrectAuthority);
    }
    if !instruction_context.is_instruction_account_signer(2)? {
        ic_logger_msg!(log_collector, "Authority did not sign");
        return Err(InstructionError::MissingRequiredSignature);
    }
```

**File:** programs/bpf_loader/src/lib.rs (L1021-1027)
```rust
    let mut close_account = instruction_context.try_borrow_instruction_account(0)?;
    let mut recipient_account = instruction_context.try_borrow_instruction_account(1)?;

    recipient_account.checked_add_lamports(close_account.get_lamports())?;
    close_account.set_lamports(0)?;
    close_account.set_state(&UpgradeableLoaderState::Uninitialized)?;
    Ok(())
```

**File:** transaction-context/src/instruction_accounts.rs (L119-128)
```rust
    /// Overwrites the number of lamports of this account (transaction wide)
    pub fn set_lamports(&mut self, lamports: u64) -> Result<(), InstructionError> {
        // An account not owned by the program cannot have its balance decrease
        if !self.is_owned_by_current_program() && lamports < self.get_lamports() {
            return Err(InstructionError::ExternalAccountLamportSpend);
        }
        // The balance of read-only may not change
        if !self.is_writable() {
            return Err(InstructionError::ReadonlyLamportChange);
        }
```

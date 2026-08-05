Audit Report

## Title
`Close` instruction lets anyone drain lamports from an `Uninitialized` BPF Loader Upgradeable account with no authority or signer check - (File: `programs/bpf_loader/src/lib.rs`)

## Summary
In `process_loader_upgradeable_instruction`'s `Close` arm, the `UpgradeableLoaderState::Uninitialized` branch unconditionally transfers all lamports from account #0 to whatever account #1 the caller supplies, without any authority-match or signer check [1](#0-0) . This is inconsistent with the `Buffer` and `ProgramData` branches, which route through `common_close_account` and require a matching, signing authority before any lamports move [2](#0-1) .

## Finding Description
The `Close` handler first ensures account #0 and account #1 differ, then dispatches on the deserialized state of account #0 [3](#0-2) . For `Buffer` and `ProgramData` states, `common_close_account` enforces that the stored `authority_address` is `Some`, matches the pubkey supplied in account #2, and that account #2 signed the transaction [4](#0-3) . The `Uninitialized` branch has no equivalent gate: it simply moves `close_account.get_lamports()` into the recipient and zeroes the source, logging the closure, with no check of who submitted the instruction [5](#0-4) . Because `UpgradeableLoaderState::Uninitialized` carries no authority field by design, there is structurally nothing for this branch to check against — but that also means no signer/ownership assertion exists at all for this code path.

An account can be `Uninitialized` while holding lamports whenever it has been created/funded and assigned to `bpf_loader_upgradeable` but `InitializeBuffer` has not yet been (or will never be) invoked in the same transaction, since `InitializeBuffer` is what actually transitions a fresh account out of `Uninitialized` [6](#0-5) . This mirrors how `common_close_account` itself resets closed `Buffer`/`ProgramData` accounts back to `Uninitialized` state [7](#0-6) , confirming `Uninitialized` is a legitimate, reachable state for loader-owned accounts outside of brand-new, never-funded ones.

## Impact Explanation
Any lamports resting in a loader-owned, `Uninitialized` account can be swept to an attacker-chosen recipient by an unrelated, non-signing party, because the `Close` instruction's `Uninitialized` branch performs no authority or signer verification. This is a direct, unprivileged fund-theft primitive reachable via a single permissionless `Close` instruction naming the victim account as account #0 and the attacker's own account as account #1.

## Likelihood Explanation
Exploitability is gated on the existence of a loader-v3-owned, lamport-funded account still in the `Uninitialized` state at attack time — i.e., a window between account creation/funding/ownership-assignment to `bpf_loader_upgradeable` and the subsequent `InitializeBuffer` call. Because nothing in the runtime enforces atomicity between those two steps, any deployment tooling, multisig/staged upload flow, or interrupted deployment that splits account creation from `InitializeBuffer` across transactions creates a directly exploitable window with a single, low-cost, permissionless transaction.

## Recommendation
Add an explicit authorization requirement to the `Uninitialized` branch of `Close` — for example, require account #0 itself to be a signer, or require the original funder/creator to be a signer — mirroring the signer/authority enforcement already present in `common_close_account` for the `Buffer` and `ProgramData` branches. Alternatively, disallow closing `Uninitialized` loader-owned accounts through this instruction entirely and require lamport recovery to go through a mechanism that verifies legitimate ownership.

## Proof of Concept
1. Create an account via `system_instruction::create_account`/`assign`, funding it with lamports and setting its owner to `bpf_loader_upgradeable`, without invoking `InitializeBuffer` in the same transaction.
2. As an unrelated attacker with no authority key, submit a transaction invoking `UpgradeableLoaderInstruction::Close` with account #0 = the victim's `Uninitialized` funded account (writable, non-signer) and account #1 = attacker's own account (writable, signer of the tx but not required to match anything on account #0).
3. `process_loader_upgradeable_instruction` matches `UpgradeableLoaderState::Uninitialized`, skips all authority/signer checks, and moves all lamports from account #0 to account #1 [1](#0-0) , confirmed by writing a Rust integration test that constructs such an account and observes the lamport transfer on `Close` with an unrelated, non-matching recipient signer.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L158-171)
```rust
        UpgradeableLoaderInstruction::InitializeBuffer => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let mut buffer = instruction_context.try_borrow_instruction_account(0)?;

            if UpgradeableLoaderState::Uninitialized != buffer.get_state()? {
                ic_logger_msg!(log_collector, "Buffer account already initialized");
                return Err(InstructionError::AccountAlreadyInitialized);
            }

            let authority_key = Some(*instruction_context.get_key_of_instruction_account(1)?);

            buffer.set_state(&UpgradeableLoaderState::Buffer {
                authority_address: authority_key,
            })?;
```

**File:** programs/bpf_loader/src/lib.rs (L686-700)
```rust
        UpgradeableLoaderInstruction::Close => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            if instruction_context.get_index_of_instruction_account_in_transaction(0)?
                == instruction_context.get_index_of_instruction_account_in_transaction(1)?
            {
                ic_logger_msg!(
                    log_collector,
                    "Recipient is the same as the account being closed"
                );
                return Err(InstructionError::InvalidArgument);
            }
            let mut close_account = instruction_context.try_borrow_instruction_account(0)?;
            let close_key = *close_account.get_key();
            let close_account_state = close_account.get_state()?;
            close_account.set_data_length(UpgradeableLoaderState::size_of_uninitialized())?;
```

**File:** programs/bpf_loader/src/lib.rs (L701-709)
```rust
            match close_account_state {
                UpgradeableLoaderState::Uninitialized => {
                    let mut recipient_account =
                        instruction_context.try_borrow_instruction_account(1)?;
                    recipient_account.checked_add_lamports(close_account.get_lamports())?;
                    close_account.set_lamports(0)?;

                    ic_logger_msg!(log_collector, "Closed Uninitialized {}", close_key);
                }
```

**File:** programs/bpf_loader/src/lib.rs (L710-716)
```rust
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

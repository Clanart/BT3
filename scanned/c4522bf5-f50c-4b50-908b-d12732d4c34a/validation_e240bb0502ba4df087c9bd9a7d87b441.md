### Title
`Close` instruction lets anyone drain lamports from an `Uninitialized` BPF Loader Upgradeable account with no authority or signer check - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
The `UpgradeableLoaderInstruction::Close` handler branches on the target account's stored state. For `Buffer` and `ProgramData` states it correctly routes through `common_close_account`, which requires a matching authority pubkey **and** that authority's signature [1](#0-0) . However, the `UpgradeableLoaderState::Uninitialized` branch skips this entirely and unconditionally sweeps all lamports from account #0 to whatever account #1 the caller supplies, with no ownership, authority, or signer check whatsoever [2](#0-1) .

### Finding Description
`process_loader_upgradeable_instruction`'s `Close` arm only enforces that recipient (#1) and closed account (#0) differ, then dispatches on the closed account's state [3](#0-2) :

```rust
UpgradeableLoaderState::Uninitialized => {
    let mut recipient_account =
        instruction_context.try_borrow_instruction_account(1)?;
    recipient_account.checked_add_lamports(close_account.get_lamports())?;
    close_account.set_lamports(0)?;

    ic_logger_msg!(log_collector, "Closed Uninitialized {}", close_key);
}
``` [2](#0-1) 

Unlike the `Buffer` and `ProgramData` branches, which call `common_close_account` and require:
- a non-`None` authority to exist,
- the authority pubkey passed in account #2 to match it, and
- account #2 to be a transaction signer [4](#0-3) 

the `Uninitialized` branch has **no equivalent check at all**. Since `Uninitialized` accounts carry no `authority_address` field in `UpgradeableLoaderState`, the only reason this branch omits the check appears to be "there is nothing to check against" — but that also means there is no gate preventing an arbitrary, non-signing third party from naming any writable `Uninitialized` loader-owned account as account #0 and redirecting its lamports to an attacker-chosen recipient at account #1.

An account can legitimately sit in the `Uninitialized` state while holding lamports: `bpf_loader_upgradeable`-owned accounts are `Uninitialized` from creation until `InitializeBuffer` is called [5](#0-4) , and `common_close_account` itself resets a closed `Buffer`/`ProgramData` account back to `Uninitialized` state (though with lamports already zeroed in that specific path) [6](#0-5) . Any account creation flow that funds and assigns ownership to `bpf_loader_upgradeable` without atomically calling `InitializeBuffer` in the same transaction (e.g., staged/multi-transaction deployment tooling, pre-funded buffer addresses, or a future/alternate client that splits `create_account` and `InitializeBuffer` across transactions) leaves a window where such an account is `Uninitialized` yet lamport-bearing and therefore exposed to this unauthenticated drain.

This is the direct analog of the external report's concern: "the functions are not always verifying the sender... other kinds of bugs could let the wrong people destruct a contract" and "if they hold tokens... locked forever" — here the inverse failure mode occurs: lamports are not locked, they are stolen by an unauthorized party because the destructive (`Close`) action for one specific state variant performs no sender verification.

### Impact Explanation
Any lamports resting in a loader-owned `Uninitialized` account can be stolen outright by an unrelated, non-signing attacker, since the `Close` instruction requires no signature or authority match in that code path. This is a direct fund-theft primitive reachable by any unprivileged transaction sender who can identify a target account in this state — no malicious validator, leaked key, or privileged role is required.

### Likelihood Explanation
Exploitability depends entirely on the existence of `Uninitialized`, lamport-funded, loader-v3-owned accounts on-chain at attack time. Because current first-party CLI/RPC deploy flows bundle account creation and `InitializeBuffer` together, the natural window is narrow, but nothing in the runtime enforces atomicity between account creation/funding and `InitializeBuffer` — any alternate deployment tooling, intermediate multi-transaction flow, or account left mid-setup is directly exploitable with a single permissionless transaction.

### Recommendation
Require an explicit authorization check even for the `Uninitialized` branch of `Close` — e.g., require account #0 itself (or its designated creator/owner) to be a signer, or disallow closing `Uninitialized` accounts entirely via this instruction and instead route lamport recovery through a system-program-level mechanism that already enforces ownership. At minimum, mirror the signer/authority checks used by `common_close_account` for consistency across all branches.

### Proof of Concept
1. Create (via `system_instruction::create_account` or `assign`) an account owned by `bpf_loader_upgradeable`, funded with lamports, but do not send `InitializeBuffer` in the same transaction (e.g., a separate follow-up transaction, or a deploy pipeline that fails/aborts between the two steps).
2. As an unrelated attacker with no knowledge of any authority key, submit a transaction invoking `UpgradeableLoaderInstruction::Close` with:
   - account #0 = the victim's `Uninitialized` funded account (writable, not a signer)
   - account #1 = attacker's own account (writable, as recipient)
3. `process_loader_upgradeable_instruction` matches `UpgradeableLoaderState::Uninitialized`, performs no signer/authority check, and transfers all lamports from account #0 to account #1 [2](#0-1) .

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

### Title
Anyone can drain lamports from an uninitialized BPF Upgradeable Loader account without any signature - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
The `UpgradeableLoaderInstruction::Close` handler in the BPF Upgradeable Loader has three branches depending on the state of the account being closed: `Uninitialized`, `Buffer`, and `ProgramData`. The `Buffer` and `ProgramData` branches both route through `common_close_account`, which enforces that the account's authority is provided and has signed the transaction [1](#0-0) . The `Uninitialized` branch, however, performs **no signature or authority check at all** before sweeping the account's lamports to an attacker-chosen recipient [2](#0-1) .

### Finding Description
This mirrors the structure of the reported bug class: a state-machine gate (`notKilled`/authorization) that is properly enforced once an object has been "claimed"/initialized, but is completely absent during the brief window before initialization completes.

For the BPF Upgradeable Loader, the normal lifecycle for a buffer/programdata account is:
1. `system_instruction::create_account` — creates the account, assigns ownership to `bpf_loader_upgradeable`, funds it to be rent-exempt. At this point the account state is `UpgradeableLoaderState::Uninitialized`.
2. `UpgradeableLoaderInstruction::InitializeBuffer` (or `DeployWithMaxDataLen`/`Write`, etc.) — sets the `authority_address`, transitioning the account out of `Uninitialized`.

If these two steps are not atomic (e.g., separate transactions, or a transaction that fails/gets dropped between them, or simply a delay a wallet/tool introduces), the account sits in the network in the `Uninitialized` state while already owned by `bpf_loader_upgradeable` and funded with lamports.

During this window, `Close` requires only 2 accounts — the account to close and the lamport recipient — and no signer check whatsoever [3](#0-2) . Contrast this with the `Buffer` state which mandates 3 accounts and calls `common_close_account`, explicitly requiring the authority to be present and to have signed instruction account index 2 [1](#0-0) .

Because there is no such check for `Uninitialized`, **any unprivileged party** can construct a transaction naming the victim's not-yet-initialized loader account as account 0 and their own account as account 1, and drain 100% of its lamports — exactly analogous to the reported `kill()` bug where a permissionless call during an unprotected startup window disrupts/damages the target before its protections (validators / authority) are established.

### Impact Explanation
This is a fund-theft primitive, not merely a griefing/DoS: an attacker can steal the lamports funding any account that is momentarily in the `Uninitialized` state while owned by `bpf_loader_upgradeable` (e.g., the rent-exempt balance placed there in preparation for a buffer/programdata account), with zero signatures or authorization from the account's rightful controller. This fits the "fund theft/loss" category of valid impact for unprivileged transaction/CPI-path bugs.

### Likelihood Explanation
Exploitation only requires observing (via any block explorer, mempool, or simply scanning existing accounts) a loader-owned account that is `Uninitialized` before its `InitializeBuffer`/deploy step lands — a routine and unremarkable state that programs, CLIs, or SDK helpers may pass through, especially if `create_account` and initialize are not submitted atomically in a single transaction. No malicious peer/validator/admin/leaked-key/front-running assumption is required: the attacker simply crafts an ordinary standalone `Close` instruction, at any point while the target account remains `Uninitialized`.

### Recommendation
Require that the `Uninitialized`-close branch also validate a signer/authority for the funds being swept — e.g., require the payer/creator of the account (or a designated authority recorded at `create_account` time) to sign the `Close` instruction, or otherwise disallow `Close` on `Uninitialized` accounts unless the closing party can prove control over the lamports (for example by requiring the recipient account to also be the original funder and to sign).

### Proof of Concept
1. Victim creates account `V` via `system_instruction::create_account`, assigning owner `bpf_loader_upgradeable::id()` and funding it to rent-exemption, intending to call `InitializeBuffer` in a follow-up transaction.
2. Before the victim's `InitializeBuffer` transaction lands (or if it is delayed/dropped), attacker submits:
   ```
   UpgradeableLoaderInstruction::Close
   accounts: [V (writable, not signer), attacker_recipient (writable, not signer)]
   ```
   as observed in the handler at [3](#0-2) .
3. Because `V.get_state()` returns `Uninitialized`, the handler unconditionally moves `V`'s full lamport balance to `attacker_recipient` and zeroes `V`, with no signature check performed anywhere in this branch — unlike the `Buffer`/`ProgramData` branches which call `common_close_account` and enforce authority + signer [1](#0-0) .
4. The victim's funds are stolen, and the intended buffer/program deployment is permanently disrupted.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L686-709)
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

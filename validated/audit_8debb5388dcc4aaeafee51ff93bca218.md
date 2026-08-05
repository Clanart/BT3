### Title
Unprotected `InitializeBuffer` instruction allows front-running to hijack buffer authority and drain rent lamports - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
The `bpf_loader_upgradeable` program's `InitializeBuffer` instruction sets the buffer's `authority_address` from whichever pubkey is supplied as instruction account index 1, without requiring that account to be a signer, and without binding the initialization to the account creator/payer. This mirrors the reported `L2EthToken.initialize` bug class: any actor who calls `InitializeBuffer` first — before the legitimate deployer does — becomes the buffer authority, since the buffer account (created separately via `system_instruction::create_account`) is a normal, publicly-addressable, uninitialized account with no linkage enforcing that only its creator may initialize it.

### Finding Description
`process_loader_upgradeable_instruction` handles `UpgradeableLoaderInstruction::InitializeBuffer` by reading the target account's state, confirming it is `Uninitialized`, and then writing `authority_address` from account index 1 with **no signer check at all**: [1](#0-0) 

Compare this with every other authority-mutating instruction in the same file (`Write`, `SetAuthority`, `SetAuthorityChecked`, `Close`/`common_close_account`), which all explicitly require `is_instruction_account_signer` on the authority account: [2](#0-1) [3](#0-2) 

The broken invariant: creation of the buffer account (`system_instruction::create_account`, funded and signed by the legitimate payer/buffer keypair) and initialization of its authority (`InitializeBuffer`) are two separate instructions that CAN be, and in ad-hoc client code often are, submitted as two separate transactions. The `create_buffer` helper used by the CLI/tests bundles them atomically in one transaction/signature set: [4](#0-3) 
but nothing in the on-chain program enforces this atomicity — a user or tool that creates the buffer account first and initializes it later (e.g. across two RPC calls) exposes a window in which the account is on-chain, owned by `bpf_loader_upgradeable`, and `Uninitialized`. Because `InitializeBuffer` requires only 2 non-signer account metas, any attacker who observes the created (but not-yet-initialized) buffer address can submit their own `InitializeBuffer` instruction naming themselves as `authority_address`, with zero signatures from the true owner needed. The guard added at `test_bpf_loader_upgradeable_initialize_buffer` only prevents *re*-initialization (`AccountAlreadyInitialized`) — it does nothing to stop the *first* initializer from being someone other than the account's creator: [5](#0-4) 

Once the attacker controls `authority_address`, they inherit every privilege gated on that authority:
- `Write` (append attacker-controlled bytecode to the buffer) — gated only by matching `authority_address` + signer, both attacker-controlled now: [6](#0-5) 
- `SetAuthority`/`SetAuthorityChecked` (permanently lock out the legitimate owner): [7](#0-6) 
- `Close` (drain the buffer's rent-exempt lamports — funded by the legitimate payer — to any recipient the attacker chooses): [8](#0-7) 

### Impact Explanation
This is fund theft: the legitimate payer funds the buffer account to be rent-exempt, but an attacker who wins the initialization race can call `Close` with themselves as authority and route those lamports to an address they control. The attacker also gains the ability to overwrite the buffer with malicious bytecode, defeating the deployer's intent for that specific buffer, and blocking the legitimate deploy flow entirely (any subsequent `Write`/`DeployWithMaxDataLen` from the true owner fails with `IncorrectAuthority`). No malicious validator, leaked key, or trusted-party assumption is required — this is a plain unprivileged instruction-crafting attack exploitable by any network participant who can observe a buffer account's public key and its on-chain state.

### Likelihood Explanation
Likelihood depends on whether callers split account creation and initialization across transactions instead of using the atomic `create_buffer` helper. Solana's CLI and the in-repo test/bench helpers construct create+initialize as one transaction (`create_buffer`, `program-test/tests/builtins.rs`), which is safe. However, the vulnerability is present at the program level regardless of tooling — the instruction itself performs no signer/authority check, so any third-party tool, wallet, or manual instruction composition that separates these steps (e.g., pre-funding a buffer address in one step, initializing later) is exploitable. This is analogous to the original report: the flaw is latent and depends on client-side sequencing, but the on-chain program provides no protection whatsoever, exactly mirroring the "unprotected initialization" class.

### Recommendation
Require the account being initialized (or a designated creator/payer account) to be a signer on `InitializeBuffer`, or bind buffer initialization to the same transaction as account creation via a CPI-based combined instruction, so that a `Program`-derived or creator-authenticated key is enforced rather than trusting an arbitrary non-signer account meta for `authority_address`. At minimum, add a signer check on account index 0 (the buffer itself) analogous to how `Write`/`SetAuthority`/`Close` require a signer for authority mutations.

### Proof of Concept
1. Legitimate user submits transaction T1: `system_instruction::create_account(payer, buffer_pubkey, lamports=rent_exempt, space, owner=bpf_loader_upgradeable::id())`, signed by `payer` and `buffer_keypair`. This transaction lands on-chain; `buffer_pubkey` is now owned by the loader and in `UpgradeableLoaderState::Uninitialized`.
2. Before the user's follow-up transaction (`InitializeBuffer` naming themselves as authority) confirms, an attacker observes `buffer_pubkey` (public data) and submits `UpgradeableLoaderInstruction::InitializeBuffer` with account metas `[buffer_pubkey (writable, non-signer), attacker_pubkey (non-signer)]`, per `programs/bpf_loader/src/lib.rs:158-172`.
3. The instruction succeeds (state was `Uninitialized`), setting `authority_address = Some(attacker_pubkey)` with no signature required from `attacker_pubkey` or from the buffer's rightful owner.
4. The legitimate user's own `InitializeBuffer` transaction now fails with `AccountAlreadyInitialized`, and their `Write` calls fail with `IncorrectAuthority`.
5. Attacker submits `Close` with `authority_address = attacker_pubkey` as signer, per `common_close_account` (`programs/bpf_loader/src/lib.rs:1003-1027`), draining the buffer's lamports (funded by the original payer) to an attacker-controlled recipient.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L158-172)
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
        }
```

**File:** programs/bpf_loader/src/lib.rs (L177-190)
```rust
            if let UpgradeableLoaderState::Buffer { authority_address } = buffer.get_state()? {
                if authority_address.is_none() {
                    ic_logger_msg!(log_collector, "Buffer is immutable");
                    return Err(InstructionError::Immutable); // TODO better error code
                }
                let authority_key = Some(*instruction_context.get_key_of_instruction_account(1)?);
                if authority_address != authority_key {
                    ic_logger_msg!(log_collector, "Incorrect buffer authority provided");
                    return Err(InstructionError::IncorrectAuthority);
                }
                if !instruction_context.is_instruction_account_signer(1)? {
                    ic_logger_msg!(log_collector, "Buffer authority did not sign");
                    return Err(InstructionError::MissingRequiredSignature);
                }
```

**File:** programs/bpf_loader/src/lib.rs (L556-576)
```rust
                UpgradeableLoaderState::Buffer { authority_address } => {
                    if new_authority.is_none() {
                        ic_logger_msg!(log_collector, "Buffer authority is not optional");
                        return Err(InstructionError::IncorrectAuthority);
                    }
                    if authority_address.is_none() {
                        ic_logger_msg!(log_collector, "Buffer is immutable");
                        return Err(InstructionError::Immutable);
                    }
                    if authority_address != Some(*present_authority_key) {
                        ic_logger_msg!(log_collector, "Incorrect buffer authority provided");
                        return Err(InstructionError::IncorrectAuthority);
                    }
                    if !instruction_context.is_instruction_account_signer(1)? {
                        ic_logger_msg!(log_collector, "Buffer authority did not sign");
                        return Err(InstructionError::MissingRequiredSignature);
                    }
                    account.set_state(&UpgradeableLoaderState::Buffer {
                        authority_address: new_authority.cloned(),
                    })?;
                }
```

**File:** programs/bpf_loader/src/lib.rs (L1003-1027)
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

    let mut close_account = instruction_context.try_borrow_instruction_account(0)?;
    let mut recipient_account = instruction_context.try_borrow_instruction_account(1)?;

    recipient_account.checked_add_lamports(close_account.get_lamports())?;
    close_account.set_lamports(0)?;
    close_account.set_state(&UpgradeableLoaderState::Uninitialized)?;
    Ok(())
```

**File:** programs/bpf_loader/src/lib.rs (L1450-1460)
```rust
        // Case: Already initialized
        let accounts = process_instruction(
            &loader_id,
            &instruction_data,
            vec![
                (buffer_address, accounts.first().unwrap().clone()),
                (authority_address, accounts.get(1).unwrap().clone()),
            ],
            instruction_accounts,
            Err(InstructionError::AccountAlreadyInitialized),
        );
```

**File:** program-test/tests/builtins.rs (L24-38)
```rust
    let create_buffer_instructions = solana_loader_v3_interface::instruction::create_buffer(
        &payer.pubkey(),
        &buffer_keypair.pubkey(),
        &upgrade_authority_keypair.pubkey(),
        buffer_rent,
        1,
    )
    .unwrap();

    let mut transaction =
        Transaction::new_with_payer(&create_buffer_instructions[..], Some(&payer.pubkey()));
    transaction.sign(&[&payer, &buffer_keypair], recent_blockhash);

    // Act
    banks_client.process_transaction(transaction).await.unwrap();
```

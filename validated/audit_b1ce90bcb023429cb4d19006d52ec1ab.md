### Title
Unauthenticated `InitializeBuffer` instruction lets an attacker front-run buffer creation and steal control (and rent lamports) of a victim's program buffer account - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
The `UpgradeableLoaderInstruction::InitializeBuffer` handler in the BPF Upgradeable Loader sets the `authority_address` of a freshly-allocated buffer account purely from whichever transaction reaches it first, with no signature check on the buffer account or the proposed authority. This mirrors the C4 finding for `CoreFactory.createProject`: an unprivileged "claim ownership" call can be front-run by anyone watching the mempool, letting the attacker become the account's authority and later drain its funds.

### Finding Description
`process_loader_upgradeable_instruction` handles `InitializeBuffer` as follows: [1](#0-0) 

The only checks performed are (1) the buffer account must currently be `Uninitialized`, and (2) an authority pubkey is read from instruction account index 1. Critically:
- Account 0 (the buffer) is **not required to be a signer**.
- Account 1 (the proposed authority) is **not required to be a signer**.
- There is no check that the caller is the entity that funded/created the buffer account via `system_instruction::create_account`.

In normal CLI usage, `create_buffer` bundles `system_instruction::create_account` and `InitializeBuffer` into instructions of the same message so they execute atomically, e.g. as seen in `do_process_program_deploy`/write-buffer flow: [2](#0-1)  and the lower-level helper [3](#0-2) . However, nothing in the protocol enforces this atomicity — the `system_instruction::create_account` step (which requires the buffer keypair's signature) and the `InitializeBuffer` step (which requires no signatures at all) are independent instructions that can be split across separate transactions, or a legitimate user's `InitializeBuffer` transaction can simply be beaten to the same slot/block by an attacker's competing transaction targeting the same (already-created, still-`Uninitialized`) buffer pubkey.

Once an attacker's `InitializeBuffer` transaction lands first, the buffer's `authority_address` becomes the attacker's key. The account is now `AccountAlreadyInitialized` for the legitimate follow-up transaction, which fails. The attacker, now holding the authority, can then call `Close` to drain the buffer's lamports to any recipient they choose, since `Close` only validates the current authority (which is now the attacker's) via `common_close_account`: [4](#0-3) 

### Impact Explanation
An attacker who wins this race becomes sole authority over the victim's buffer account and can subsequently `Close` it to redirect all lamports (the rent-exemption deposit the victim paid to create the account, which can be substantial for large program buffers) to an account of the attacker's choosing. This is a direct loss-of-funds and loss-of-control primitive analogous to the referenced report's "malicious user becomes owner and withdraws funds."

### Likelihood Explanation
Exploitability depends on an attacker being able to observe an in-flight `system_instruction::create_account` (owner = `bpf_loader_upgradeable`) targeting a specific buffer pubkey before its paired `InitializeBuffer` instruction is confirmed, and then submitting a competing `InitializeBuffer` transaction that lands first. This requires mempool visibility and transaction landing control (a leader/searcher advantage), and is mitigated in the common case where wallets/CLIs bundle both instructions atomically in one transaction (as agave's own CLI does). It is not exploitable against strictly atomic single-transaction flows, only against callers who split account creation and initialization across transactions or otherwise expose the intermediate `Uninitialized`-but-created state.

### Recommendation
Require the buffer account itself to be a signer on `InitializeBuffer` (proving the caller controls the keypair that was just used to create it), or require the caller to co-sign as the intended authority, closing the front-runnable initialization window. Alternatively, document/require that `InitializeBuffer` only ever be submitted atomically with the preceding `CreateAccount` instruction within the same transaction, and reject `InitializeBuffer` instructions where the buffer account is not also present as a signer in the same transaction.

### Proof of Concept
1. Victim submits an unconfirmed transaction: `system_instruction::create_account(payer, buffer_pubkey, lamports, space, owner=bpf_loader_upgradeable::id())` in one transaction, planning to send a follow-up `InitializeBuffer { }` transaction naming themselves as authority.
2. Attacker observes the pending/confirmed `CreateAccount` output (buffer account now exists, owned by `bpf_loader_upgradeable`, state `Uninitialized`) before the victim's `InitializeBuffer` transaction confirms.
3. Attacker submits their own transaction invoking `InitializeBuffer` against the same `buffer_pubkey`, listing their own pubkey as instruction account 1 (`authority`) — no signature from the buffer account or the authority is required per [1](#0-0) .
4. If the attacker's transaction lands first, the buffer's `authority_address` becomes the attacker's key; the victim's subsequent `InitializeBuffer` transaction fails with `AccountAlreadyInitialized`.
5. Attacker calls `Close` on the buffer with themselves as authority and any recipient pubkey, draining the buffer's lamports per [5](#0-4) .

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

**File:** programs/bpf_loader/src/lib.rs (L686-716)
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
                UpgradeableLoaderState::Buffer { authority_address } => {
                    instruction_context.check_number_of_instruction_accounts(3)?;
                    drop(close_account);
                    common_close_account(&authority_address, &instruction_context, &log_collector)?;

                    ic_logger_msg!(log_collector, "Closed Buffer {}", close_key);
                }
```

**File:** cli/src/program.rs (L2707-2726)
```rust
) -> ProcessResult {
    let blockhash = rpc_client.get_latest_blockhash().await?;
    let compute_unit_limit = ComputeUnitLimit::Simulated;

    let (initial_instructions, balance_needed, buffer_program_data) =
        if let Some(buffer_program_data) = buffer_program_data {
            (vec![], 0, buffer_program_data)
        } else {
            (
                loader_v3_instruction::create_buffer(
                    &fee_payer_signer.pubkey(),
                    buffer_pubkey,
                    &buffer_authority_signer.pubkey(),
                    min_rent_exempt_program_buffer_balance,
                    program_len,
                )?,
                min_rent_exempt_program_buffer_balance,
                vec![0; program_len],
            )
        };
```

**File:** runtime/src/loader_utils.rs (L88-107)
```rust
    bank_client
        .send_and_confirm_message(
            &[from_keypair, buffer_keypair],
            Message::new(
                &solana_loader_v3_interface::instruction::create_buffer(
                    &from_keypair.pubkey(),
                    &buffer_pubkey,
                    &buffer_authority_pubkey,
                    1.max(
                        bank_client
                            .get_minimum_balance_for_rent_exemption(program_buffer_bytes)
                            .unwrap(),
                    ),
                    program.len(),
                )
                .unwrap(),
                Some(&from_keypair.pubkey()),
            ),
        )
        .unwrap();
```

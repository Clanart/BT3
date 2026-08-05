## Title
Missing explicit ownership check on Program account in `DeployWithMaxDataLen` allows in-flight state corruption before failure - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
The external report flags Serum's `create_market.rs` for omitting `check_account_owner` on `base_vault`/`quote_vault` before using them, letting attacker-supplied accounts of the wrong owner flow deep into instruction logic before any failure occurs. The Agave analog is in the BPF Loader Upgradeable program's `DeployWithMaxDataLen` handler: unlike the sibling `Upgrade` instruction (which explicitly checks `program.get_owner() != program_id`), `DeployWithMaxDataLen` never validates that the "Program account" (`instruction_accounts[2]`) is owned by `bpf_loader_upgradeable` before operating on it.

### Finding Description
In `process_loader_upgradeable_instruction`, the `Upgrade` branch explicitly verifies ownership of the program account: [1](#0-0) 

The `DeployWithMaxDataLen` branch, by contrast, borrows the Program account and checks state/size/rent-exemption, but never checks `program.get_owner() != program_id`: [2](#0-1) 

The handler proceeds to drain the Buffer account's lamports to the payer, CPI into the System program to create the ProgramData account, run ELF verification/deployment (`deploy_program!`), and only then attempt `program.set_state(...)` / `program.set_executable(true)` on the unchecked Program account: [3](#0-2) 

Because ownership was never checked up front, all of that work (buffer drain, System CPI account creation, ELF loading/verification) executes against a mismatched invariant before the omission is ever detected. The only place the omission surfaces is deep in `can_data_be_changed()`, invoked transitively by `set_state()`, which enforces `is_owned_by_current_program()`: [4](#0-3) 

This is confirmed by an inline test comment in the codebase itself, explicitly documenting the gap and contrasting it with `Upgrade`: [5](#0-4) 
The same comment block also notes a companion gap: no explicit writability check either, surfacing only via `ReadonlyDataModified`: [6](#0-5) 

### Impact Explanation
Under the current code path, the instruction ultimately does fail with `ExternalAccountDataModified` (via the deferred `can_data_be_changed` check), so no funds are stolen and no bad state is committed on this path today — this mirrors the DEX report's own characterization ("additional checks should be included") rather than a confirmed drain. However, the missing early-owner-check is a broken invariant with real risk:
- It relies entirely on an incidental, deep enforcement path (`set_state`'s implicit ownership guard) rather than an explicit guard at the point of use — the same anti-pattern flagged in the external report.
- The buffer-draining side effect (`payer.checked_add_lamports(buffer.get_lamports()); buffer.set_lamports(0)`) and the System-program CPI to create the ProgramData account both execute *before* the ownership violation is ever detected, meaning any future refactor that reorders operations, changes `set_state`'s internal checks, or removes the implicit guard could silently turn this into an unauthorized-account-mutation or fund-movement bug with no explicit test coverage protecting the owner invariant itself.
- This exactly matches the report's broken invariant: "trust an account's role/type without checking its owner," deferring detection to incidental side effects deep in execution rather than validating up front.

### Likelihood Explanation
Low-to-moderate. The bug is not independently exploitable today because the deferred check in `can_data_be_changed()` still catches the mismatch and aborts the instruction, and Solana's runtime additionally reverts all account changes on instruction failure. The likelihood concern is structural/defense-in-depth: this code lacks the same explicit ownership check its sibling instruction (`Upgrade`) has, and only an incidental internal invariant (not an explicit guard) currently prevents state corruption or fund loss if that invariant is ever weakened.

### Recommendation
Add an explicit ownership check on the Program account in the `DeployWithMaxDataLen` branch, symmetric with the `Upgrade` branch:
```rust
let program = instruction_context.try_borrow_instruction_account(2)?;
if program.get_owner() != program_id {
    ic_logger_msg!(log_collector, "Program account not owned by loader");
    return Err(InstructionError::IncorrectProgramId);
}
if !program.is_writable() {
    ic_logger_msg!(log_collector, "Program account not writeable");
    return Err(InstructionError::InvalidArgument);
}
```
placed before the existing state/size/rent checks at [2](#0-1) , so the invariant is validated at the point of use rather than relying on the incidental `set_state` guard.

### Proof of Concept
The repository's own test suite already demonstrates the gap and its current (safe, but incidental) outcome: [7](#0-6) 
Setting the Program account's owner to an arbitrary pubkey and invoking `DeployWithMaxDataLen` causes the buffer-drain and System-program CPI to execute normally, and the mismatch is only caught at the final `program.set_state(...)` call, yielding `InstructionError::ExternalAccountDataModified` instead of an explicit `IncorrectProgramId` rejection at the start of the handler — confirming that ownership is validated only incidentally and after side effects have already occurred, not explicitly at the entry point as it should be.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L213-229)
```rust
            // Verify Program account

            let program = instruction_context.try_borrow_instruction_account(2)?;
            if UpgradeableLoaderState::Uninitialized != program.get_state()? {
                ic_logger_msg!(log_collector, "Program account already initialized");
                return Err(InstructionError::AccountAlreadyInitialized);
            }
            if program.get_data().len() < UpgradeableLoaderState::size_of_program() {
                ic_logger_msg!(log_collector, "Program account too small");
                return Err(InstructionError::AccountDataTooSmall);
            }
            if program.get_lamports() < rent.minimum_balance(program.get_data().len()) {
                ic_logger_msg!(log_collector, "Program account not rent-exempt");
                return Err(InstructionError::ExecutableAccountNotRentExempt);
            }
            let new_program_id = *program.get_key();
            drop(program);
```

**File:** programs/bpf_loader/src/lib.rs (L287-363)
```rust
            // Drain the Buffer account to payer before paying for programdata account
            {
                let mut buffer = instruction_context.try_borrow_instruction_account(3)?;
                let mut payer = instruction_context.try_borrow_instruction_account(0)?;
                payer.checked_add_lamports(buffer.get_lamports())?;
                buffer.set_lamports(0)?;
            }

            let owner_id = *program_id;
            let mut instruction = system_instruction::create_account(
                &payer_key,
                &programdata_key,
                1.max(rent.minimum_balance(programdata_len)),
                programdata_len as u64,
                program_id,
            );

            // pass an extra account to avoid the overly strict UnbalancedInstruction error
            instruction
                .accounts
                .push(AccountMeta::new(buffer_key, false));

            invoke_context
                .native_invoke_signed(instruction, &[&[new_program_id.as_ref(), &[bump_seed]]])?;

            // Load and verify the program bits
            let transaction_context = &invoke_context.transaction_context;
            let instruction_context = transaction_context.get_current_instruction_context()?;
            let buffer = instruction_context.try_borrow_instruction_account(3)?;
            deploy_program!(
                invoke_context,
                &new_program_id,
                &owner_id,
                buffer
                    .get_data()
                    .get(buffer_data_offset..)
                    .ok_or(InstructionError::AccountDataTooSmall)?,
                clock.slot,
                invoke_context
                    .get_feature_set()
                    .disable_sbpf_v0_v1_v2_deployment,
            );
            drop(buffer);

            let transaction_context = &invoke_context.transaction_context;
            let instruction_context = transaction_context.get_current_instruction_context()?;

            // Update the ProgramData account and record the program bits
            {
                let mut programdata = instruction_context.try_borrow_instruction_account(1)?;
                programdata.set_state(&UpgradeableLoaderState::ProgramData {
                    slot: clock.slot,
                    upgrade_authority_address: authority_key,
                })?;
                let dst_slice = programdata
                    .get_data_mut()?
                    .get_mut(
                        programdata_data_offset
                            ..programdata_data_offset.saturating_add(buffer_data_len),
                    )
                    .ok_or(InstructionError::AccountDataTooSmall)?;
                let mut buffer = instruction_context.try_borrow_instruction_account(3)?;
                let src_slice = buffer
                    .get_data()
                    .get(buffer_data_offset..)
                    .ok_or(InstructionError::AccountDataTooSmall)?;
                dst_slice.copy_from_slice(src_slice);
                buffer.set_data_length(UpgradeableLoaderState::size_of_buffer(0))?;
            }

            // Update the Program account
            let mut program = instruction_context.try_borrow_instruction_account(2)?;
            program.set_state(&UpgradeableLoaderState::Program {
                programdata_address: programdata_key,
            })?;
            program.set_executable(true)?;
            drop(program);
```

**File:** programs/bpf_loader/src/lib.rs (L379-387)
```rust
            let program = instruction_context.try_borrow_instruction_account(1)?;
            if !program.is_writable() {
                ic_logger_msg!(log_collector, "Program account not writeable");
                return Err(InstructionError::InvalidArgument);
            }
            if program.get_owner() != program_id {
                ic_logger_msg!(log_collector, "Program account not owned by loader");
                return Err(InstructionError::IncorrectProgramId);
            }
```

**File:** programs/bpf_loader/src/lib.rs (L2584-2609)
```rust
        // Case: Program account not owned by loader
        //
        // Unlike `Upgrade`, `DeployWithMaxDataLen` has no explicit owner
        // check on the program account. Validation passes, and the failure
        // only surfaces at the end when the handler tries to mutate the
        // program's state — `set_state` requires the account to be owned by
        // the currently-executing program, so it trips
        // `ExternalAccountDataModified`.
        let (mut transaction_accounts, instruction_accounts) = get_accounts(
            &payer_address,
            &buffer_address,
            &upgrade_authority_address,
            &upgrade_authority_address,
            &elf,
        );
        transaction_accounts
            .get_mut(2)
            .unwrap()
            .1
            .set_owner(Pubkey::new_unique());
        process_instruction(
            elf.len(),
            transaction_accounts,
            instruction_accounts,
            Err(InstructionError::ExternalAccountDataModified),
        );
```

**File:** programs/bpf_loader/src/lib.rs (L2611-2630)
```rust
        // Case: Program account not writable
        //
        // `DeployWithMaxDataLen` also lacks an explicit writability check on
        // the program account, so the failure again surfaces at
        // `set_state`, this time via the writability guard: a non-writable
        // account yields `ReadonlyDataModified`.
        let (transaction_accounts, mut instruction_accounts) = get_accounts(
            &payer_address,
            &buffer_address,
            &upgrade_authority_address,
            &upgrade_authority_address,
            &elf,
        );
        instruction_accounts.get_mut(2).unwrap().is_writable = false;
        process_instruction(
            elf.len(),
            transaction_accounts,
            instruction_accounts,
            Err(InstructionError::ReadonlyDataModified),
        );
```

**File:** transaction-context/src/instruction_accounts.rs (L329-348)
```rust
    /// Returns true if the owner of this account is the current `InstructionContext`s last program (instruction wide)
    pub fn is_owned_by_current_program(&self) -> bool {
        self.transaction_context
            .get_key_of_account_at_index(self.index_in_transaction_of_instruction_program)
            .map(|program_key| program_key == self.get_owner())
            .unwrap_or_default()
    }

    /// Returns an error if the account data can not be mutated by the current program
    pub fn can_data_be_changed(&self) -> Result<(), InstructionError> {
        // and only if the account is writable
        if !self.is_writable() {
            return Err(InstructionError::ReadonlyDataModified);
        }
        // and only if we are the owner
        if !self.is_owned_by_current_program() {
            return Err(InstructionError::ExternalAccountDataModified);
        }
        Ok(())
    }
```

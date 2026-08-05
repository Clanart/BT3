[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** program-runtime/src/invoke_context.rs (L319-324)
```rust
    /// Entrypoint for a cross-program invocation from a builtin program.
    ///
    /// Takes signer seeds and derives PDAs internally via
    /// `create_program_address`, mirroring the SBF CPI path. This makes
    /// it structurally impossible for a builtin to vouch for a non-PDA
    /// address (e.g. a user wallet) as a signer.
```

**File:** program-runtime/src/invoke_context.rs (L330-340)
```rust
        let caller_program_id = *self
            .transaction_context
            .get_current_instruction_context()?
            .get_program_key()?;
        // The conversion from `PubkeyError` to `InstructionError` through
        // num-traits is incorrect, but it's the existing behavior.
        let signers = signer_seeds
            .iter()
            .map(|seeds| Pubkey::create_program_address(seeds, &caller_program_id))
            .collect::<Result<Vec<Pubkey>, solana_pubkey::PubkeyError>>()
            .map_err(|e| e as u64)?;
```

**File:** program-runtime/src/invoke_context.rs (L349-496)
```rust
    pub(crate) fn prepare_next_cpi_instruction(
        &mut self,
        instruction: Instruction,
        signers: &[Pubkey],
    ) -> Result<(), InstructionError> {
        // We reference accounts by an u8 index, so we have a total of 256 accounts.
        let transaction_callee_map_len = (self.transaction_context.get_number_of_accounts()
            as usize)
            .min(MAX_ACCOUNTS_PER_TRANSACTION);
        let mut transaction_callee_map: Vec<u16> = vec![u16::MAX; transaction_callee_map_len];
        let mut instruction_accounts: Vec<InstructionAccount> =
            Vec::with_capacity(instruction.accounts.len());

        // This code block is necessary to restrict the scope of the immutable borrow of
        // transaction context (the `instruction_context` variable). At the end of this
        // function, we must borrow it again as mutable.
        let program_account_index = {
            let instruction_context = self.transaction_context.get_current_instruction_context()?;

            for account_meta in instruction.accounts.iter() {
                let index_in_transaction = self
                    .transaction_context
                    .find_index_of_account(&account_meta.pubkey)
                    .ok_or_else(|| {
                        ic_msg!(
                            self,
                            "Instruction references an unknown account {}",
                            account_meta.pubkey,
                        );
                        InstructionError::MissingAccount
                    })?;

                debug_assert!((index_in_transaction as usize) < transaction_callee_map.len());
                let index_in_callee = transaction_callee_map
                    .get_mut(index_in_transaction as usize)
                    .unwrap();

                if (*index_in_callee as usize) < instruction_accounts.len() {
                    let cloned_account = {
                        let instruction_account = instruction_accounts
                            .get_mut(*index_in_callee as usize)
                            .ok_or(InstructionError::MissingAccount)?;
                        instruction_account.set_is_signer(
                            instruction_account.is_signer() || account_meta.is_signer,
                        );
                        instruction_account.set_is_writable(
                            instruction_account.is_writable() || account_meta.is_writable,
                        );
                        *instruction_account
                    };
                    instruction_accounts.push(cloned_account);
                } else {
                    *index_in_callee = instruction_accounts.len() as u16;
                    instruction_accounts.push(InstructionAccount::new(
                        index_in_transaction,
                        account_meta.is_signer,
                        account_meta.is_writable,
                    ));
                }
            }

            for current_index in 0..instruction_accounts.len() {
                let instruction_account = instruction_accounts.get(current_index).unwrap();
                let index_in_callee = *transaction_callee_map
                    .get(instruction_account.index_in_transaction as usize)
                    .unwrap() as usize;

                if current_index != index_in_callee {
                    let (is_signer, is_writable) = {
                        let reference_account = instruction_accounts
                            .get(index_in_callee)
                            .ok_or(InstructionError::MissingAccount)?;
                        (
                            reference_account.is_signer(),
                            reference_account.is_writable(),
                        )
                    };

                    let current_account = instruction_accounts.get_mut(current_index).unwrap();
                    current_account.set_is_signer(current_account.is_signer() || is_signer);
                    current_account.set_is_writable(current_account.is_writable() || is_writable);
                    // This account is repeated, so there is no need to check for permissions
                    continue;
                }

                let index_in_caller = instruction_context.get_index_of_account_in_instruction(
                    instruction_account.index_in_transaction,
                )?;

                // This unwrap is safe because instruction.accounts.len() == instruction_accounts.len()
                let account_key = &instruction.accounts.get(current_index).unwrap().pubkey;
                // get_index_of_account_in_instruction has already checked if the index is valid.
                let caller_instruction_account = instruction_context
                    .instruction_accounts()
                    .get(index_in_caller as usize)
                    .unwrap();

                // Readonly in caller cannot become writable in callee
                if instruction_account.is_writable() && !caller_instruction_account.is_writable() {
                    ic_msg!(self, "{}'s writable privilege escalated", account_key,);
                    return Err(InstructionError::PrivilegeEscalation);
                }

                // To be signed in the callee,
                // it must be either signed in the caller or by the program
                if instruction_account.is_signer()
                    && !(caller_instruction_account.is_signer() || signers.contains(account_key))
                {
                    ic_msg!(self, "{}'s signer privilege escalated", account_key,);
                    return Err(InstructionError::PrivilegeEscalation);
                }
            }

            // Find and validate executables / program accounts
            let callee_program_id = &instruction.program_id;
            let program_account_index_in_transaction = self
                .transaction_context
                .find_index_of_account(callee_program_id);
            let program_account_index_in_instruction = program_account_index_in_transaction
                .map(|index| instruction_context.get_index_of_account_in_instruction(index));

            // We first check if the account exists in the transaction, and then see if it is part
            // of the instruction.
            if program_account_index_in_instruction.is_none()
                || program_account_index_in_instruction.unwrap().is_err()
            {
                ic_msg!(self, "Unknown program {}", callee_program_id);
                return Err(InstructionError::MissingAccount);
            }

            // SAFETY: This unwrap is safe, because we checked the index in instruction in the
            // previous if-condition.
            program_account_index_in_transaction.unwrap()
        };

        // This ? operator should not error out because `fn get_current_instruction_index` is also called
        // in `get_current_instruction_context`
        let caller_index = self.transaction_context.get_current_instruction_index()?;
        self.transaction_context.configure_instruction_at_index(
            self.transaction_context.get_instruction_trace_length(),
            program_account_index,
            instruction_accounts,
            transaction_callee_map,
            Cow::Owned(instruction.data),
            Some(caller_index as u16),
        )?;
        Ok(())
    }
```

**File:** program-runtime/src/invoke_context.rs (L1989-2017)
```rust
    // CPI marks an account as signer but caller provides no seeds —
    // signer privilege escalation.
    #[test]
    fn test_native_invoke_signed_pda_privilege_escalation_without_seeds() {
        let (pda_key, _bump_seed) =
            Pubkey::find_program_address(&[b"seed"], &TEST_CALLER_PROGRAM_ID);
        let instruction = Instruction::new_with_bincode(
            TEST_CALLEE_PROGRAM_ID,
            &MockInstruction::NoopSuccess,
            vec![AccountMeta::new(pda_key, true)],
        );
        let result = run_native_invoke_signed_test(pda_key, false, instruction, &[]);
        assert_eq!(result, Err(InstructionError::PrivilegeEscalation));
    }

    // Seeds valid for a different program ID don't grant signer privilege
    // because native_invoke_signed derives against the caller's own program ID.
    #[test]
    fn test_native_invoke_signed_uses_caller_program_id_for_pda() {
        let (pda_key, bump_seed) = Pubkey::find_program_address(&[b"seed"], &TEST_WRONG_PROGRAM_ID);
        let instruction = Instruction::new_with_bincode(
            TEST_CALLEE_PROGRAM_ID,
            &MockInstruction::NoopSuccess,
            vec![AccountMeta::new(pda_key, true)],
        );
        let result =
            run_native_invoke_signed_test(pda_key, false, instruction, &[&[b"seed", &[bump_seed]]]);
        assert_eq!(result, Err(InstructionError::PrivilegeEscalation));
    }
```

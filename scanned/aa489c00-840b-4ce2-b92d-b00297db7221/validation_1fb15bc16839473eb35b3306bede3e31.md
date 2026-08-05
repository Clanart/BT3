[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** transaction-context/src/transaction.rs (L248-252)
```rust
    /// Gets instruction stack height, top-level instructions are height
    /// `solana_instruction::TRANSACTION_LEVEL_STACK_HEIGHT`
    pub fn get_instruction_stack_height(&self) -> usize {
        self.instruction_stack.len()
    }
```

**File:** transaction-context/src/transaction.rs (L406-458)
```rust
    /// Pushes the next instruction
    pub fn push(&mut self) -> Result<(), InstructionError> {
        let nesting_level = self.get_instruction_stack_height();
        if !self.instruction_stack.is_empty() && self.accounts.get_lamports_delta() != 0 {
            return Err(InstructionError::UnbalancedInstruction);
        }
        {
            let instruction = self
                .instruction_trace
                .last_mut()
                .ok_or(InstructionError::CallDepth)?;
            instruction.nesting_level = nesting_level as u16;
        }

        if self.number_of_called_instructions_in_trace() >= self.instruction_trace_capacity {
            return Err(InstructionError::MaxInstructionTraceLengthExceeded);
        }

        let (index_in_trace, current_top_level_instruction) = if self.instruction_stack.is_empty() {
            let index = self.next_top_level_instruction_index;
            self.next_top_level_instruction_index =
                self.next_top_level_instruction_index.saturating_add(1);
            (index, index)
        } else {
            let index = self.get_instruction_trace_length();
            self.transaction_frame.number_of_cpis_in_trace = self
                .transaction_frame
                .number_of_cpis_in_trace
                .saturating_add(1);
            self.instruction_trace.push(InstructionFrame::default());
            (
                index,
                self.next_top_level_instruction_index.saturating_sub(1),
            )
        };

        if nesting_level >= self.instruction_stack_capacity {
            return Err(InstructionError::CallDepth);
        }
        self.transaction_frame.current_executing_instruction = index_in_trace as u16;
        self.instruction_stack.push(index_in_trace);
        if let Some(index_in_transaction) = self.find_index_of_account(&instructions::id()) {
            let mut mut_account_ref = self.accounts.try_borrow_mut(index_in_transaction)?;
            if mut_account_ref.owner() != &solana_sdk_ids::sysvar::id() {
                return Err(InstructionError::InvalidAccountOwner);
            }
            instructions::store_current_index_checked(
                mut_account_ref.data_as_mut_slice(),
                current_top_level_instruction as u16,
            )?;
        }
        Ok(())
    }
```

**File:** transaction-context/src/transaction.rs (L460-493)
```rust
    /// Pops the current instruction
    pub fn pop(&mut self) -> Result<(), InstructionError> {
        if self.instruction_stack.is_empty() {
            return Err(InstructionError::CallDepth);
        }
        // Verify (before we pop) that the total sum of all lamports in this instruction did not change
        let detected_an_unbalanced_instruction =
            self.get_current_instruction_context()
                .and_then(|instruction_context| {
                    // Verify all executable accounts have no outstanding refs
                    self.accounts
                        .try_borrow_mut(
                            instruction_context.get_index_of_program_account_in_transaction()?,
                        )
                        .map_err(|err| {
                            if err == InstructionError::AccountBorrowFailed {
                                InstructionError::AccountBorrowOutstanding
                            } else {
                                err
                            }
                        })?;
                    Ok(self.accounts.get_lamports_delta() != 0)
                });
        // Always pop, even if we `detected_an_unbalanced_instruction`
        self.instruction_stack.pop();
        if let Some(instr_idx) = self.instruction_stack.last() {
            self.transaction_frame.current_executing_instruction = *instr_idx as u16;
        }
        if detected_an_unbalanced_instruction? {
            Err(InstructionError::UnbalancedInstruction)
        } else {
            Ok(())
        }
    }
```

**File:** program-runtime/src/invoke_context.rs (L273-311)
```rust
    /// Push a stack frame onto the invocation stack
    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    fn push(&mut self) -> Result<(), InstructionError> {
        let instruction_context = self.transaction_context.get_next_instruction_context()?;
        let program_id = instruction_context
            .get_program_key()
            .map_err(|_| InstructionError::UnsupportedProgramId)?;
        if self.transaction_context.get_instruction_stack_height() != 0 {
            let contains =
                (0..self.transaction_context.get_instruction_stack_height()).any(|level| {
                    self.transaction_context
                        .get_instruction_context_at_nesting_level(level)
                        .and_then(|instruction_context| instruction_context.get_program_key())
                        .map(|program_key| program_key == program_id)
                        .unwrap_or(false)
                });
            let is_last = self
                .transaction_context
                .get_current_instruction_context()
                .and_then(|instruction_context| instruction_context.get_program_key())
                .map(|program_key| program_key == program_id)
                .unwrap_or(false);
            if contains && !is_last {
                // Reentrancy not allowed unless caller is calling itself
                return Err(InstructionError::ReentrancyNotAllowed);
            }
        }

        self.transaction_context.push()?;
        self.memory_contexts.push_placeholder();
        Ok(())
    }

    /// Pop a stack frame from the invocation stack
    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    fn pop(&mut self) -> Result<(), InstructionError> {
        self.memory_contexts.pop();
        self.transaction_context.pop()
    }
```

**File:** program-runtime/src/invoke_context.rs (L1359-1372)
```rust
        // At exactly max_depth, one more push must fail with CallDepth.
        assert_eq!(invoke_context.get_stack_height(), max_depth);
        invoke_context
            .transaction_context
            .configure_top_level_instruction_for_tests(
                (first_program_account.saturating_add(max_depth)) as IndexOfAccount,
                instruction_accounts.clone(),
                vec![],
            )
            .unwrap();
        assert_eq!(invoke_context.push(), Err(InstructionError::CallDepth),);

        // Stack height must not have changed after the rejected push.
        assert_eq!(invoke_context.get_stack_height(), max_depth);
```

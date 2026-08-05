[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** program-runtime/src/invoke_context.rs (L275-304)
```rust
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
```

**File:** program-runtime/src/invoke_context.rs (L307-311)
```rust
    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    fn pop(&mut self) -> Result<(), InstructionError> {
        self.memory_contexts.pop();
        self.transaction_context.pop()
    }
```

**File:** program-runtime/src/memory_context.rs (L90-93)
```rust
    pub fn push_placeholder(&mut self) {
        // We are only pushing a placeholder to be configured later
        self.contexts.push(MemoryContextType::Placeholder);
    }
```

**File:** program-runtime/src/memory_context.rs (L95-97)
```rust
    pub fn pop(&mut self) {
        self.contexts.pop();
    }
```

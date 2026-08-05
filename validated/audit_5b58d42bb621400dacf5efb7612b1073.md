[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** program-runtime/src/invoke_context.rs (L19-19)
```rust
        memory_context::{MemoryContext, MemoryContexts},
```

**File:** program-runtime/src/invoke_context.rs (L27-34)
```rust
    solana_sbpf::{
        ebpf::MM_HEAP_START,
        elf::{ElfError, Executable as GenericExecutable},
        error::{EbpfError, ProgramResult},
        memory_region::MemoryMapping,
        program::{BuiltinProgram, SBPFVersion},
        vm::{Config, ContextObject, EbpfVm},
    },
```

**File:** program-runtime/src/invoke_context.rs (L45-48)
```rust
    solana_transaction_context::{
        IndexOfAccount, MAX_ACCOUNTS_PER_TRANSACTION, instruction::InstructionContext,
        instruction_accounts::InstructionAccount, transaction::TransactionContext,
    },
```

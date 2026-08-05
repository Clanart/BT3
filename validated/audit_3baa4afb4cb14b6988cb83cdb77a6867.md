[1](#0-0)

### Citations

**File:** runtime/src/installed_scheduler_pool.rs (L23-40)
```rust
use {
    crate::bank::Bank,
    log::*,
    solana_clock::Slot,
    solana_hash::Hash,
    solana_runtime_transaction::runtime_transaction::RuntimeTransaction,
    solana_svm_timings::ExecuteTimings,
    solana_transaction::sanitized::SanitizedTransaction,
    solana_transaction_error::{TransactionError, TransactionResult as Result},
    solana_unified_scheduler_logic::OrderedTaskId,
    std::{
        fmt::{self, Debug},
        mem,
        ops::Deref,
        sync::{Arc, RwLock},
        thread,
    },
};
```

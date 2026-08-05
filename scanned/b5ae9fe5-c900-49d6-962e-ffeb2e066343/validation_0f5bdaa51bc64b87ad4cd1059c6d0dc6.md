[1](#0-0)

### Citations

**File:** program-runtime/src/loaded_programs.rs (L1-23)
```rust
use {
    crate::{
        invoke_context::InvokeContext,
        loading_task::LoadingTaskWaiter,
        program_cache_entry::{
            ProgramCacheEntry, ProgramCacheEntryOwner, ProgramCacheEntryType, retention_score,
        },
        program_metrics::{EMA_SCALE, ProgramCacheStats},
    },
    log::error,
    solana_clock::{Epoch, Slot},
    solana_pubkey::Pubkey,
    solana_sbpf::program::BuiltinProgram,
    solana_svm_type_overrides::{
        rand::{Rng, rng},
        sync::{Arc, Mutex, RwLock, atomic::Ordering},
        thread,
    },
    std::{
        collections::{HashMap, hash_map::Entry},
        sync::Weak,
    },
};
```

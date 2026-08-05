[1](#0-0)

### Citations

**File:** ledger/src/blockstore/error.rs (L101-106)
```rust
    #[error("invalid parent info for slot {slot}: parent {parent_slot}, max root {root}")]
    InvalidParentInfo {
        slot: Slot,
        parent_slot: Slot,
        root: Slot,
    },
```

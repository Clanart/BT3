[1](#0-0) [2](#0-1)

### Citations

**File:** rpc/src/rpc.rs (L1459-1474)
```rust
        let end_slot = min(
            end_slot.unwrap_or_else(|| start_slot.saturating_add(MAX_GET_CONFIRMED_BLOCKS_RANGE)),
            if commitment.is_finalized() {
                highest_super_majority_root
            } else {
                self.get_bank_with_config(config)?.slot()
            },
        );
        if end_slot < start_slot {
            return Ok(vec![]);
        }
        if end_slot - start_slot > MAX_GET_CONFIRMED_BLOCKS_RANGE {
            return Err(Error::invalid_params(format!(
                "Slot range too large; max {MAX_GET_CONFIRMED_BLOCKS_RANGE}"
            )));
        }
```

**File:** ledger/src/blockstore.rs (L1-1)
```rust
//! The `blockstore` module provides functions for parallel verification of the
```

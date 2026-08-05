[1](#0-0) [2](#0-1)

### Citations

**File:** ledger/src/next_slots_iterator.rs (L1-18)
```rust
use {
    crate::{blockstore::*, blockstore_meta::SlotMeta},
    solana_clock::Slot,
};

pub struct NextSlotsIterator<'a> {
    pending_slots: Vec<Slot>,
    blockstore: &'a Blockstore,
}

impl<'a> NextSlotsIterator<'a> {
    pub fn new(start_slot: Slot, blockstore: &'a Blockstore) -> Self {
        Self {
            pending_slots: vec![start_slot],
            blockstore,
        }
    }
}
```

**File:** ledger/src/next_slots_iterator.rs (L20-35)
```rust
impl Iterator for NextSlotsIterator<'_> {
    type Item = (Slot, SlotMeta);
    fn next(&mut self) -> Option<Self::Item> {
        if self.pending_slots.is_empty() {
            None
        } else {
            let slot = self.pending_slots.pop().unwrap();
            if let Some(slot_meta) = self.blockstore.meta(slot).unwrap() {
                self.pending_slots.extend(slot_meta.next_slots.iter());
                Some((slot, slot_meta))
            } else {
                None
            }
        }
    }
}
```

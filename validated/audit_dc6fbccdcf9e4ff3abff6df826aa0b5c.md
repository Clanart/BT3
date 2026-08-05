[1](#0-0) [2](#0-1)

### Citations

**File:** core/src/repair/repair_handler.rs (L148-156)
```rust
        let ancestor_slot_hashes = if self.blockstore().is_duplicate_confirmed(slot) {
            let ancestor_iterator = AncestorIteratorWithHash::from(
                AncestorIterator::new_inclusive(slot, self.blockstore()),
            );
            ancestor_iterator.take(MAX_ANCESTOR_RESPONSES).collect()
        } else {
            // If this slot is not duplicate confirmed, return nothing
            vec![]
        };
```

**File:** ledger/src/ancestor_iterator.rs (L30-47)
```rust
impl Iterator for AncestorIterator<'_> {
    type Item = Slot;

    fn next(&mut self) -> Option<Self::Item> {
        let current = self.current;
        current.inspect(|&slot| {
            if slot != 0 {
                self.current = self
                    .blockstore
                    .meta(slot)
                    .unwrap()
                    .and_then(|slot_meta| slot_meta.parent_slot);
            } else {
                self.current = None;
            }
        })
    }
}
```

[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** accounts-db/src/accounts_index/iter.rs (L7-14)
```rust
/// Iterator over AccountsIndex
///
/// One bin is inspected per call to `next()`, and bins may be empty.
/// Thus, clients must be able to handle `next()` returning `Some(vec![])`.
pub struct AccountsIndexPubkeyIterator<'a, T: IndexValue, U: DiskIndexValue + From<T> + Into<T>> {
    account_maps: &'a [Arc<InMemAccountsIndex<T, U>>],
    current_bin: usize,
}
```

**File:** accounts-db/src/accounts_index/iter.rs (L32-40)
```rust
    fn next(&mut self) -> Option<Self::Item> {
        if self.current_bin < self.account_maps.len() {
            let items = self.account_maps[self.current_bin].keys();
            self.current_bin += 1;
            Some(items)
        } else {
            None
        }
    }
```

**File:** accounts-db/src/accounts_index/iter.rs (L60-83)
```rust
    /// Ensure exhausted iterator continues to return None if new entries are added to the index.
    #[test]
    fn test_exhausted() {
        let index = AccountsIndex::<bool, bool>::default_for_tests();
        let mut iter = index.iter();
        for _items in iter.by_ref() {
            // exhaust the iterator
        }
        assert!(iter.next().is_none());

        // add a new entry to the index
        let mut gc = ReclaimsSlotList::new();
        index.upsert(
            0,
            0,
            &solana_pubkey::new_rand(),
            true,
            &mut gc,
            UpsertReclaim::ReclaimOldSlots,
        );

        // ensure the iterator remains exhausted
        assert!(iter.next().is_none());
    }
```

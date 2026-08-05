[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** accounts-db/src/accounts_index/iter.rs (L7-10)
```rust
/// Iterator over AccountsIndex
///
/// One bin is inspected per call to `next()`, and bins may be empty.
/// Thus, clients must be able to handle `next()` returning `Some(vec![])`.
```

**File:** accounts-db/src/accounts_index/iter.rs (L27-41)
```rust
/// Implement the Iterator trait for AccountsIndexIterator
impl<T: IndexValue, U: DiskIndexValue + From<T> + Into<T>> Iterator
    for AccountsIndexPubkeyIterator<'_, T, U>
{
    type Item = Vec<Pubkey>;
    fn next(&mut self) -> Option<Self::Item> {
        if self.current_bin < self.account_maps.len() {
            let items = self.account_maps[self.current_bin].keys();
            self.current_bin += 1;
            Some(items)
        } else {
            None
        }
    }
}
```

[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** accounts-db/src/accounts_index/iter.rs (L11-14)
```rust
pub struct AccountsIndexPubkeyIterator<'a, T: IndexValue, U: DiskIndexValue + From<T> + Into<T>> {
    account_maps: &'a [Arc<InMemAccountsIndex<T, U>>],
    current_bin: usize,
}
```

**File:** accounts-db/src/accounts_index/iter.rs (L19-24)
```rust
    pub fn new(index: &'a AccountsIndex<T, U>) -> Self {
        Self {
            account_maps: &index.account_maps,
            current_bin: 0,
        }
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

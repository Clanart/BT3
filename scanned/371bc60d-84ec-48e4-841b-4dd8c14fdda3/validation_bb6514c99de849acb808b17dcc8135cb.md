[1](#0-0) [1](#0-0) [2](#0-1)

### Citations

**File:** entry/src/entry.rs (L213-228)
```rust
    /// Creates the next Entry `num_hashes` after `start_hash`.
    pub fn new(prev_hash: &Hash, mut num_hashes: u64, transactions: Vec<Transaction>) -> Self {
        // If you passed in transactions, but passed in num_hashes == 0, then
        // next_hash will generate the next hash and set num_hashes == 1
        if num_hashes == 0 && !transactions.is_empty() {
            num_hashes = 1;
        }

        let transactions = transactions.into_iter().map(Into::into).collect::<Vec<_>>();
        let hash = next_hash(prev_hash, num_hashes, &transactions);
        Entry {
            num_hashes,
            hash,
            transactions,
        }
    }
```

**File:** entry/src/entry.rs (L549-561)
```rust
/// Creates the next Tick or Transaction Entry `num_hashes` after `start_hash`.
pub fn next_versioned_entry(
    prev_hash: &Hash,
    num_hashes: u64,
    transactions: Vec<VersionedTransaction>,
) -> Entry {
    assert!(num_hashes > 0 || transactions.is_empty());
    Entry {
        num_hashes,
        hash: next_hash(prev_hash, num_hashes, &transactions),
        transactions,
    }
}
```

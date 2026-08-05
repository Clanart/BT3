[1](#0-0) [2](#0-1)

### Citations

**File:** accounts-db/src/append_vec.rs (L52-64)
```rust
/// size of the fixed sized fields in an append vec
/// we need to add data len and align it to get the actual stored size
pub const STORE_META_OVERHEAD: usize = 136;

// Ensure the STORE_META_OVERHEAD constant remains accurate
const _: () = assert!(
    STORE_META_OVERHEAD
        == mem::size_of::<StoredMeta>()
            + mem::size_of::<AccountMeta>()
            + mem::size_of::<ObsoleteAccountHash>()
);

pub const MAXIMUM_APPEND_VEC_FILE_SIZE: u64 = 16 * 1024 * 1024 * 1024; // 16 GiB
```

**File:** accounts-db/src/accounts_file.rs (L204-217)
```rust
    /// Calculate the amount of storage required for an account with the passed
    /// in data_len
    pub(crate) fn calculate_stored_size(&self, data_len: usize) -> usize {
        match self {
            Self::AppendVec(_) => AppendVec::calculate_stored_size(data_len),
        }
    }

    /// for each offset in `sorted_offsets`, get the data size
    pub(crate) fn get_account_data_lens(&self, sorted_offsets: &[usize]) -> Vec<usize> {
        match self {
            Self::AppendVec(av) => av.get_account_data_lens(sorted_offsets),
        }
    }
```

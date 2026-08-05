[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** accounts-db/src/account_storage/stored_account_info.rs (L10-17)
```rust
pub struct StoredAccountInfo<'storage> {
    pub pubkey: &'storage Pubkey,
    pub lamports: u64,
    pub owner: &'storage Pubkey,
    pub data: &'storage [u8],
    pub executable: bool,
    pub rent_epoch: Epoch,
}
```

**File:** accounts-db/src/append_vec.rs (L64-85)
```rust
pub const MAXIMUM_APPEND_VEC_FILE_SIZE: u64 = 16 * 1024 * 1024 * 1024; // 16 GiB

pub type Result<T> = std::result::Result<T, AppendVecError>;

/// An enum for AppendVec related errors.
#[derive(Error, Debug)]
pub enum AppendVecError {
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),

    #[error("too small file size {0} for AppendVec")]
    FileSizeTooSmall(usize),

    #[error("too large file size {0} for AppendVec")]
    FileSizeTooLarge(usize),

    #[error("incorrect layout/length/data in the appendvec at path {}", .0.display())]
    IncorrectLayout(PathBuf),

    #[error("offset ({0}) is larger than file size ({1})")]
    OffsetOutOfBounds(usize, usize),
}
```

**File:** accounts-db/src/append_vec.rs (L87-102)
```rust
/// A slice whose contents are known to be valid.
/// The slice contains no undefined bytes.
#[derive(Debug, Copy, Clone)]
struct ValidSlice<'a>(&'a [u8]);

impl<'a> ValidSlice<'a> {
    #[inline(always)]
    fn new(data: &'a [u8]) -> Self {
        Self(data)
    }

    #[inline(always)]
    fn len(&self) -> usize {
        self.0.len()
    }
}
```

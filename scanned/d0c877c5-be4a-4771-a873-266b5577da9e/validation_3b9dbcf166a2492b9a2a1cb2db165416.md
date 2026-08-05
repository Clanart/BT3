[1](#0-0)

### Citations

**File:** accounts-db/src/append_vec.rs (L1-13)
```rust
//! Persistent storage for accounts.
//!
//! For more information, see:
//!
//! <https://docs.anza.xyz/implemented-proposals/persistent-account-storage>

mod meta;
pub mod test_utils;

#[cfg(feature = "dev-context-only-utils")]
pub use meta::StoredAccountMeta;
#[cfg(not(feature = "dev-context-only-utils"))]
use meta::StoredAccountMeta;
```

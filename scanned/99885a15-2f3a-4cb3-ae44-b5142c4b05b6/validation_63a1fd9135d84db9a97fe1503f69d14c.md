[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** accounts-db/src/append_vec/meta.rs (L11-20)
```rust
pub struct StoredMeta {
    /// global write version
    /// This will be made completely obsolete such that we stop storing it.
    /// We will not support multiple append vecs per slot anymore, so this concept is no longer necessary.
    /// Order of stores of an account to an append vec will determine 'latest' account data per pubkey.
    pub write_version_obsolete: u64,
    pub data_len: u64,
    /// key for the account
    pub pubkey: Pubkey,
}
```

**File:** accounts-db/src/append_vec/meta.rs (L150-173)
```rust
    #[cfg(feature = "dev-context-only-utils")]
    pub fn sanitize_executable(&self) -> bool {
        // Sanitize executable to ensure higher 7-bits are cleared correctly.
        self.ref_executable_byte() & !1 == 0
    }

    /// Check if the account data matches that of a default account.
    ///
    /// Note that we are not comparing against AccountSharedData::default() because we do not have access to the account data,
    /// so we compare data _length_ in lieu of actual data. This check otherwise identical to AccountSharedData::default().
    #[cfg(feature = "dev-context-only-utils")]
    pub fn is_default_account(&self) -> bool {
        self.account_meta.lamports == 0
            && self.meta.data_len == 0
            && !self.account_meta.executable
            && self.account_meta.rent_epoch == Epoch::default()
            && self.account_meta.owner == Pubkey::default()
    }

    #[cfg(feature = "dev-context-only-utils")]
    pub fn sanitize_lamports(&self) -> bool {
        // Check if the account data matches that of a default account if it has 0 lamports.
        self.account_meta.lamports != 0 || self.is_default_account()
    }
```

**File:** accounts-db/src/account_storage/stored_account_info.rs (L20-23)
```rust
    pub fn pubkey(&self) -> &Pubkey {
        self.pubkey
    }
}
```

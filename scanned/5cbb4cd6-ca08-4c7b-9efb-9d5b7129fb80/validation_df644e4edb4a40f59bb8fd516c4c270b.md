[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** accounts-db/src/accounts_db.rs (L3290-3306)
```rust
        // Step 1: Pre-scan the cache index to find the newest visible cached version of each
        // pubkey. Hold the Arc<CachedAccount> to keep the data alive even if the cache flushes
        // between now and step 3 (Arc clone is just a refcount bump).
        let cached_pubkeys = self.accounts_cache.cached_pubkeys();
        let mut cached_versions =
            HashMap::with_capacity_and_hasher(cached_pubkeys.len(), PubkeyHasherBuilder::default());
        for pubkey in cached_pubkeys {
            if config.is_aborted() {
                break;
            }

            if let Some((cached_account, slot)) =
                self.accounts_cache.load_latest(&pubkey, ancestors)
            {
                cached_versions.insert(pubkey, (cached_account, slot));
            }
        }
```

**File:** accounts-db/src/accounts_scan.rs (L41-55)
```rust
    /// use existing 'abort' if available, otherwise allocate one
    pub(crate) fn recreate_with_abort(&self) -> Self {
        ScanConfig {
            abort: Some(self.abort.clone().unwrap_or_default()),
        }
    }

    /// true if scan should abort
    pub(crate) fn is_aborted(&self) -> bool {
        if let Some(abort) = self.abort.as_ref() {
            abort.load(Ordering::Relaxed)
        } else {
            false
        }
    }
```

**File:** accounts-db/src/accounts.rs (L367-394)
```rust
    /// Return true iff sum > 'byte_limit_for_scan'
    fn accumulate_and_check_scan_result_size(
        sum: &AtomicUsize,
        account: &AccountSharedData,
        byte_limit_for_scan: &Option<usize>,
    ) -> bool {
        if let Some(byte_limit_for_scan) = byte_limit_for_scan.as_ref() {
            let added = Self::calc_scan_result_size(account);
            sum.fetch_add(added, Ordering::Relaxed)
                .saturating_add(added)
                > *byte_limit_for_scan
        } else {
            false
        }
    }

    fn maybe_abort_scan(
        result: ScanResult<Vec<KeyedAccountSharedData>>,
        config: &ScanConfig,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        if config.is_aborted() {
            ScanResult::Err(ScanError::Aborted(
                "The accumulated scan results exceeded the limit".to_string(),
            ))
        } else {
            result
        }
    }
```

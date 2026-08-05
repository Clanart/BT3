[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** accounts-db/src/read_only_accounts_cache.rs (L43-47)
```rust
    /// 'slot' tracks when the 'account' is stored. This important for
    /// correctness. When 'loading' from the cache by pubkey+slot, we need to
    /// make sure that both pubkey and slot matches in the cache. Otherwise, we
    /// may return the wrong account.
    slot: Slot,
```

**File:** accounts-db/src/read_only_accounts_cache.rs (L160-182)
```rust
    pub(crate) fn load(&self, pubkey: Pubkey, slot: Slot) -> Option<AccountSharedData> {
        let (account, load_us) = measure_us!({
            let mut found = None;
            if let Some(entry) = self.cache.get(&pubkey)
                && entry.slot == slot
            {
                entry
                    .last_update_time
                    .store(self.timestamp(), Ordering::Relaxed);
                let account = entry.account.clone();
                drop(entry);
                self.stats.hits.fetch_add(1, Ordering::Relaxed);
                found = Some(account);
            }

            if found.is_none() {
                self.stats.misses.fetch_add(1, Ordering::Relaxed);
            }
            found
        });
        self.stats.load_us.fetch_add(load_us, Ordering::Relaxed);
        account
    }
```

**File:** accounts-db/src/read_only_accounts_cache.rs (L188-222)
```rust
    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    pub(crate) fn store(&self, pubkey: Pubkey, slot: Slot, account: AccountSharedData) {
        self.store_with_timestamp(pubkey, slot, account, self.timestamp())
    }

    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    fn store_with_timestamp(
        &self,
        pubkey: Pubkey,
        slot: Slot,
        account: AccountSharedData,
        timestamp: u64,
    ) {
        let measure_store = Measure::start("");
        self.highest_slot_stored.fetch_max(slot, Ordering::Release);
        let new_account_size = Self::account_size(&account);
        let old_account_size;
        match self.cache.entry(pubkey) {
            Entry::Vacant(entry) => {
                old_account_size = 0;
                entry.insert(ReadOnlyAccountCacheEntry::new(account, slot, timestamp));
                self.cache_len.fetch_add(1, Ordering::Relaxed);
            }
            Entry::Occupied(mut entry) => {
                let entry = entry.get_mut();
                old_account_size = Self::account_size(&entry.account);
                entry.account = account;
                entry.slot = slot;
                entry.last_update_time.store(timestamp, Ordering::Relaxed);
            }
        };
        update_stat(&self.data_size, old_account_size, new_account_size);
        let store_us = measure_store.end_as_us();
        self.stats.store_us.fetch_add(store_us, Ordering::Relaxed);
    }
```

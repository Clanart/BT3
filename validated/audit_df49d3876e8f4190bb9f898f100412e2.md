## No Vulnerability found for this question.

**Reasoning:**

The mechanism described is an intended design characteristic, not a defect. `ReadOnlyAccountsCache` explicitly uses a *lo/hi hysteresis* scheme: `_max_data_size_lo` and `_max_data_size_hi` are separate bounds precisely so the cache is allowed to grow beyond `_max_data_size_hi` transiently before the background evictor drains it back down to `_max_data_size_lo`. [1](#0-0)  The evictor only wakes to act once `data_size` exceeds `max_data_size_hi`, and then loops evicting until it reaches the lower target, by design absorbing bursts rather than evicting on every single store. [2](#0-1) [3](#0-2) 

`store_with_timestamp` is not reachable at an attacker-controlled arbitrary rate independent of normal transaction/block processing throughput. Each cache insertion corresponds to an account load that occurs during actual transaction execution in the runtime, which is itself already bounded per slot by the block's compute-unit limit, account-lock limits, and per-account data-size limits — none of which this cache mechanism removes or bypasses. [4](#0-3)  An attacker cannot make `store` calls occur faster than the validator's own transaction execution pipeline processes transactions, so the growth of `data_size` between polling intervals is fundamentally capped by the same throughput limits that already gate all transaction execution — it is not "unbounded by an arbitrary multiple" as the question posits.

The existing test `test_evict_in_background` already demonstrates the intended behavior: the cache is allowed to exceed the target size until the evictor thread wakes and reduces it back to the configured maximum, confirming this is expected, tested behavior rather than a bug. [5](#0-4) 

Because the observed transient overshoot is an intentional hysteresis design (not an invariant violation) and the claimed unbounded/attacker-driven growth rate is not substantiated — store throughput is inherently capped by normal transaction-processing/runtime limits already enforced elsewhere — this does not constitute a valid, distinct remote-exhaustion vulnerability.

### Citations

**File:** accounts-db/src/read_only_accounts_cache.rs (L86-90)
```rust
    cache: Arc<DashMap<ReadOnlyCacheKey, ReadOnlyAccountCacheEntry, AHashRandomState>>,
    _max_data_size_lo: usize,
    _max_data_size_hi: usize,
    data_size: Arc<AtomicUsize>,
    cache_len: Arc<AtomicUsize>,
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

**File:** accounts-db/src/read_only_accounts_cache.rs (L317-330)
```rust
                    if data_size.load(Ordering::Relaxed) <= max_data_size_hi {
                        continue;
                    }
                    stats.evict_run_count.fetch_add(1, Ordering::Relaxed);

                    #[cfg(not(feature = "dev-context-only-utils"))]
                    let (num_evicts, evict_us) = measure_us!(Self::evict(
                        max_data_size_lo,
                        &data_size,
                        &cache_len,
                        evict_sample_size,
                        &cache,
                        &mut rng,
                    ));
```

**File:** accounts-db/src/read_only_accounts_cache.rs (L370-371)
```rust
        let mut num_evicts: u64 = 0;
        while data_size.load(Ordering::Relaxed) > target_data_size {
```

**File:** accounts-db/src/read_only_accounts_cache.rs (L580-617)
```rust
    #[test_matrix([8, 10, 16])]
    fn test_evict_in_background(evict_sample_size: usize) {
        const ACCOUNT_DATA_SIZE: usize = 200;
        const MAX_ENTRIES: usize = 7;
        const MAX_CACHE_SIZE: usize = MAX_ENTRIES * (CACHE_ENTRY_SIZE + ACCOUNT_DATA_SIZE);
        let cache =
            ReadOnlyAccountsCache::new(MAX_CACHE_SIZE, MAX_CACHE_SIZE, evict_sample_size, 8);

        for i in 0..MAX_ENTRIES {
            let pubkey = Pubkey::new_unique();
            let account = AccountSharedData::new(i as u64, ACCOUNT_DATA_SIZE, &Pubkey::default());
            cache.store(pubkey, i as Slot, account);
        }
        // we haven't exceeded the max cache size yet, so no evictions should've happened
        assert_eq!(cache.cache_len(), MAX_ENTRIES);
        assert_eq!(cache.data_size(), MAX_CACHE_SIZE);
        assert_eq!(cache.stats.evicts.load(Ordering::Relaxed), 0);

        // store another account to trigger evictions
        let slot = MAX_ENTRIES as Slot;
        let pubkey = Pubkey::new_unique();
        let account = AccountSharedData::new(42, ACCOUNT_DATA_SIZE, &Pubkey::default());
        cache.store(pubkey, slot, account);

        // wait for the evictor to run...
        let timer = Instant::now();
        while cache.stats.evicts.load(Ordering::Relaxed) == 0 {
            assert!(
                timer.elapsed() < Duration::from_secs(5),
                "timed out waiting for the evictor to run",
            );
            thread::sleep(Duration::from_millis(1));
        }

        // ...now ensure the cache size is right
        assert_eq!(cache.cache_len(), MAX_ENTRIES);
        assert_eq!(cache.data_size(), MAX_CACHE_SIZE);
    }
```

## Title
Unbounded `getProgramAccounts` full scan when program-id is not in secondary account index causes single-request memory/CPU exhaustion - ([File: rpc/src/rpc.rs])

### Summary
The `in3-server` report is about a lack of per-request/per-client resource limiting for RPC methods that can force a node to do disproportionate work relative to what the client paid to request. Agave's JSON-RPC has an analogous gap: `getFilteredProgramAccounts` enforces a configurable result-size limit (`scan_results_limit_bytes`) only when the queried key happens to be covered by a secondary account index. When it is not indexed (the common case for programs not explicitly added via `--account-index`), the scan runs with no byte/size bound at all, letting a single unauthenticated client trigger an unbounded, memory-hungry accounts-db scan with one HTTP call.

### Finding Description
`JsonRpcRequestProcessor::get_filtered_program_accounts` branches on whether the target `program_id` is covered by the `AccountIndex::ProgramId` secondary index: [1](#0-0) 

- If indexed, it calls `get_filtered_indexed_accounts`, which passes `self.config.scan_results_limit_bytes` down to `Accounts::load_by_index_key_with_filter`, which tracks accumulated result size via `accumulate_and_check_scan_result_size` and aborts the scan (`ScanError::Aborted`) once the configured byte budget is exceeded: [2](#0-1) [3](#0-2) 

- If NOT indexed — the default for any `program_id` that wasn't explicitly configured with `--account-index program-id` — it calls `bank.get_filtered_program_accounts`, which delegates to `Accounts::load_by_program_with_filter`. That function uses `ScanConfig::default()` (no abort capability) and collects every matching account into an in-memory `Vec` with **no byte limit whatsoever**: [4](#0-3) 

The code even documents this asymmetry explicitly: *"this path does not need to provide a mb limit because we only want to support secondary indexes"*: [5](#0-4) 

The underlying `AccountsDb::scan_accounts` walks the entire visible accounts index/cache and invokes the callback for every account, materializing full `AccountSharedData` (including account data bytes) for every entry that matches `account.owner() == program_id`: [6](#0-5) 

Because this runs inside `spawn_blocking` on the RPC runtime, it doesn't block the async I/O loop, but it does consume a blocking-thread-pool worker and, more importantly, allocates memory proportional to the total size of every account owned by `program_id` for the entire duration of the call — for widely-used programs (e.g., SPL Token, System Program, or any popular on-chain program with millions of accounts) this can be gigabytes of allocation triggered by a single JSON-RPC POST.

### Impact Explanation
A single unauthenticated RPC client, without needing any special stake or privileges, can send one `getProgramAccounts` request naming a program with a large number of owned accounts and, because that program is not registered under the `ProgramId` secondary index (the default unless the operator explicitly opts in via `--account-index program-id`), the node performs a fully unbounded scan/allocation with no way to abort based on size. Repeated or concurrent requests can exhaust node memory or degrade the RPC service and the shared blocking-thread pool used for many other RPC calls, causing degraded or crashed RPC service on that node. This matches the "single-client low-rate RPC crash/degradation" and "non-RPC remote exhaustion" impact classes: no malicious peer/validator assumption is needed — a single ordinary RPC client is sufficient.

### Likelihood Explanation
Likelihood is high for any RPC node that enables `full_api`/`getProgramAccounts` without configuring the `ProgramId` secondary account index (the default configuration), which is a common real-world deployment (many RPC providers run without account indexes enabled, since building/maintaining them is itself expensive). No authentication, stake, or special network position is required — only a normal RPC endpoint reachable by the attacker.

### Recommendation
Apply the same `scan_results_limit_bytes` (or an always-on hard cap) to the non-indexed scan path in `Accounts::load_by_program_with_filter` / `AccountsDb::scan_accounts`, mirroring the abort-on-overflow logic already implemented for `load_by_index_key_with_filter`, so that unindexed `getProgramAccounts` calls cannot allocate unbounded memory. Additionally, consider enforcing a conservative default byte/time budget for all accounts-scan RPC methods regardless of secondary-index configuration, and/or per-IP request throttling for RPC methods that are known to trigger full-database scans (`getProgramAccounts`, `getTokenAccountsByOwner`/`ByDelegate` when unindexed), similar to the `KeyedRateLimiter`/`TokenBucket` machinery already used for QUIC/TPU connection throttling (`streamer/src/nonblocking/connection_rate_limiter.rs`, `net-utils/src/token_bucket.rs`).

### Proof of Concept
1. Run a validator/RPC node with `--full-rpc-api` and without `--account-index program-id` (the default).
2. Send a single JSON-RPC request:
```json
{"jsonrpc":"2.0","id":1,"method":"getProgramAccounts","params":["TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"]}
```
targeting a program with a very large number of owned accounts on the cluster (e.g. the SPL Token program on mainnet has tens of millions of accounts).
3. Observe that `get_filtered_program_accounts` takes the un-indexed branch (`rpc/src/rpc.rs:2283-2307`) and `Accounts::load_by_program_with_filter` (`accounts-db/src/accounts.rs:338-358`) collects every matching account into memory with no size check, causing large memory allocation and prolonged blocking-thread occupation from a single client request.

### Citations

**File:** rpc/src/rpc.rs (L309-347)
```rust
    pub async fn get_filtered_indexed_accounts(
        &self,
        bank: &Arc<Bank>,
        index_key: &IndexKey,
        program_id: &Pubkey,
        filters: Vec<RpcFilterType>,
        sort_results: bool,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        let bank = Arc::clone(bank);
        let index_key = index_key.to_owned();
        let program_id = program_id.to_owned();
        let byte_limit_for_scans = self.config.scan_results_limit_bytes;
        let mut accounts = self
            .runtime
            .spawn_blocking(move || {
                bank.get_filtered_indexed_accounts(
                    &index_key,
                    |account| {
                        // The program-id account index checks for Account owner on inclusion.
                        // However, due to the current AccountsDb implementation, an account may
                        // remain in storage as a zero-lamport AccountSharedData::Default() after
                        // being wiped and reinitialized in later updates. We include the redundant
                        // filters here to avoid returning these accounts.
                        account.owner().eq(&program_id)
                            && filters
                                .iter()
                                .all(|filter_type| filter_allows(filter_type, account))
                    },
                    byte_limit_for_scans,
                )
            })
            .await
            .expect("Failed to spawn blocking task")?;
        if sort_results {
            // Avoid copying pubkeys (using Ord::cmp(a, b) silences clippy::unnecessary_sort_by).
            accounts.sort_unstable_by(|(addr_a, _), (addr_b, _)| Ord::cmp(addr_a, addr_b));
        }
        Ok(accounts)
    }
```

**File:** rpc/src/rpc.rs (L2252-2308)
```rust
    /// Use a set of filters to get an iterator of keyed program accounts from a bank
    #[allow(clippy::result_large_err)]
    async fn get_filtered_program_accounts(
        &self,
        bank: Arc<Bank>,
        program_id: Pubkey,
        mut filters: Vec<RpcFilterType>,
        sort_results: bool,
    ) -> RpcCustomResult<Vec<(Pubkey, AccountSharedData)>> {
        optimize_filters(&mut filters);
        if self
            .config
            .account_indexes
            .contains(&AccountIndex::ProgramId)
        {
            if !self.config.account_indexes.include_key(&program_id) {
                return Err(RpcCustomError::KeyExcludedFromSecondaryIndex {
                    index_key: program_id.to_string(),
                });
            }
            self.get_filtered_indexed_accounts(
                &bank,
                &IndexKey::ProgramId(program_id),
                &program_id,
                filters,
                sort_results,
            )
            .await
            .map_err(|e| RpcCustomError::ScanError {
                message: e.to_string(),
            })
        } else {
            // this path does not need to provide a mb limit because we only want to support secondary indexes
            let mut accounts = self
                .runtime
                .spawn_blocking(move || {
                    bank.get_filtered_program_accounts(
                        &program_id,
                        |account: &AccountSharedData| {
                            filters
                                .iter()
                                .all(|filter_type| filter_allows(filter_type, account))
                        },
                    )
                    .map_err(|e| RpcCustomError::ScanError {
                        message: e.to_string(),
                    })
                })
                .await
                .expect("Failed to spawn blocking task")?;
            if sort_results {
                // Avoid copying pubkeys (using Ord::cmp(a, b) silences clippy::unnecessary_sort_by).
                accounts.sort_unstable_by(|(addr_a, _), (addr_b, _)| Ord::cmp(addr_a, addr_b));
            }
            Ok(accounts)
        }
    }
```

**File:** accounts-db/src/accounts.rs (L338-358)
```rust
    pub fn load_by_program_with_filter<F: Fn(&AccountSharedData) -> bool>(
        &self,
        ancestors: &Ancestors,
        bank_id: BankId,
        program_id: &Pubkey,
        filter: F,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        let mut collector = Vec::new();
        self.accounts_db
            .scan_accounts(
                ancestors,
                bank_id,
                |some_account_tuple| {
                    Self::load_while_filtering(&mut collector, some_account_tuple, |account| {
                        account.owner() == program_id && filter(account)
                    })
                },
                &ScanConfig::default(),
            )
            .map(|_| collector)
    }
```

**File:** accounts-db/src/accounts.rs (L360-394)
```rust
    fn calc_scan_result_size(account: &AccountSharedData) -> usize {
        account.data().len()
            + std::mem::size_of::<AccountSharedData>()
            + std::mem::size_of::<Pubkey>()
    }

    /// Accumulate size of (pubkey + account) into sum.
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

**File:** accounts-db/src/accounts_db.rs (L3259-3336)
```rust
    /// Scans all accounts visible from `ancestors`, invoking `scan_func` for each.
    /// Pre-scans the write cache to capture entries not yet flushed to the accounts index, then
    /// deduplicates against the index scan, calling `scan_func` with the newest version of each
    /// account
    pub(crate) fn scan_accounts<F>(
        &self,
        ancestors: &Ancestors,
        bank_id: BankId,
        mut scan_func: F,
        config: &ScanConfig,
    ) -> ScanResult<()>
    where
        F: FnMut(Option<(&Pubkey, AccountSharedData, Slot)>),
    {
        // Register this scan so that slots needed by the scan are not cleaned out from under us.
        let scan_guard = ScanGuard::try_new(&self.scan_tracker, bank_id, || self.max_root())
            .ok_or(ScanError::SlotRemoved {
                slot: ancestors.max_slot(),
                bank_id,
            })?;

        // If the scan's ancestors are all rooted, drop them and scan roots only
        // Scan Guard max root must be used as the scan guard guarantees that
        // the account state as of max root is persisted in the database
        let max_root_ancestors = Ancestors::from(vec![scan_guard.max_root()]);
        let ancestors = if scan_guard.should_use_ancestors(ancestors) {
            ancestors
        } else {
            &max_root_ancestors
        };

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

        // Step 2: Scan the accounts_index. For each pubkey, return the newest version found in
        // either the storage or the cache. If both versions are the same, use the cached version
        // to avoid a redundant load from storage.
        // Bound max_root by ancestors.min_slot() so that roots from slots
        // beyond the querying bank's ancestor chain are not visible.
        let mut max_root = scan_guard.max_root();
        if let Some(min) = ancestors.min_slot() {
            max_root = max_root.min(min);
        }
        self.accounts_index.scan_accounts(
            ancestors,
            max_root,
            |pubkey, (account_info, slot)| {
                if let Some((cached_account, cache_slot)) = cached_versions.remove(pubkey)
                    && cache_slot >= slot
                {
                    scan_func(Some((pubkey, cached_account.account.clone(), cache_slot)));
                    return;
                }

                let mut account_accessor =
                    self.get_account_accessor(slot, &account_info.storage_location());

                let account_slot = account_accessor.get_loaded_account(|loaded_account| {
                    (pubkey, loaded_account.take_account(), slot)
                });
                scan_func(account_slot)
            },
            config,
```

## Analysis

This confirms the analog. `getProgramAccounts` (and its derivatives `getTokenAccountsByOwner`/`getTokenAccountsByDelegate`, `getLargestAccounts`, etc.) resolve, when no secondary account index is configured, to `Bank::get_filtered_program_accounts` → `Accounts::load_by_program_with_filter` → `AccountsDb::scan_accounts`, which is invoked with a bare `ScanConfig::default()` that has **no abort/byte-limit mechanism at all** — the size accounting (`accumulate_and_check_scan_result_size` / `byte_limit_for_scan`) is only wired up in the indexed path (`load_by_index_key_with_filter`), not in the unindexed program/owner scan path. [1](#0-0) [2](#0-1) 

### Title
Unbounded, un-metered full-ledger account scan in `getProgramAccounts`-family RPC methods causes single-node compute/memory exhaustion - (File: `rpc/src/rpc.rs`, `accounts-db/src/accounts.rs`, `accounts-db/src/accounts_db.rs`)

### Summary
The report's broken invariant is: "a single unprivileged caller can force an on-chain loop over an unbounded, attacker-influenced data set with no per-call cap, exceeding platform resource limits." The Agave analog is the JSON-RPC `getProgramAccounts` / `getTokenAccountsByOwner` / `getTokenAccountsByDelegate` (and `get_filtered_program_accounts`/`get_largest_accounts`) code path: when the node does **not** have the relevant secondary `AccountIndex` enabled (the default for `ProgramId`/`SplTokenOwner`/`SplTokenMint`), a single RPC request causes `AccountsDb::scan_accounts` to walk **every account in the entire accounts database** with a `ScanConfig::default()` that carries no abort flag and no byte-limit check.

### Finding Description
`JsonRpcRequestProcessor::get_filtered_program_accounts` branches on whether `AccountIndex::ProgramId` is configured: [3](#0-2) 

- If the secondary index **is** enabled, it goes through `get_filtered_indexed_accounts`, which passes `byte_limit_for_scans` (from `--accounts-index-scan-results-limit-mb`, unset by default) and uses `ScanConfig::default().recreate_with_abort()` plus `accumulate_and_check_scan_result_size` to abort the scan once results exceed the configured byte budget. [4](#0-3) [5](#0-4) 

- If the secondary index is **not** enabled (the operator-selectable default, and the comment explicitly says "this path does not need to provide a mb limit because we only want to support secondary indexes"), it falls back to `bank.get_filtered_program_accounts` → `Accounts::load_by_program_with_filter` → `AccountsDb::scan_accounts`, invoked with a plain `ScanConfig::default()` that has `abort: None` and is never checked against any size/byte limit. [6](#0-5) [1](#0-0) [7](#0-6) 

`scan_accounts` then iterates over the accounts cache and the entire `accounts_index` for the bank, calling `scan_func` for every single account visible from the ancestors — a loop bounded only by the total number of accounts ever created in the ledger, which anyone can inflate for free by creating many zero/low-lamport accounts owned by any `program_id`, `owner`, `mint`, or `delegate` they choose. [8](#0-7) 

This mirrors the Forta `redeem()` bug precisely: an unprivileged caller triggers a single request whose cost scales linearly (or worse, given `Vec` growth + serialization) with an unbounded on-chain data set that the caller (or any third party) fully controls the size of, with **no cap enforced on the hot/default code path**, only an optional operator-configured knob (`accounts_index_scan_results_limit_mb`) that is off unless explicitly turned on, and even then only applies to the indexed branch.

### Impact Explanation
When the target account set (e.g., all SPL Token accounts, or all accounts owned by a popular system/loader program) grows large, a single `getProgramAccounts`/`getTokenAccountsByOwner`/`getTokenAccountsByDelegate` call forces the RPC node to materialize and serialize potentially the entire account set in memory on a blocking thread (`spawn_blocking`), which can exhaust memory/CPU and stall or crash that RPC node's ability to serve further requests — degrading or crashing a single client-facing RPC service from one low-rate (single) request, without needing a malicious validator, leaked keys, or any privileged access. This matches the "single-client low-rate RPC crash/degradation" and "non-RPC remote exhaustion" adjacent categories described as valid impact. It does not affect consensus, since the scan is purely a local RPC read against `Bank`/`AccountsDb` state and does not touch voting/replay.

### Likelihood Explanation
Likelihood is high for any RPC operator running with `--full-rpc-api` and without secondary account indexes enabled (a common/default configuration on public-facing endpoints), since the account population that can be targeted (e.g., total system-program-owned accounts, or all token accounts with a common owner/delegate/mint) is entirely attacker-influenced and grows organically on mainnet without any adversarial action — this is a known, long-standing operational risk with `getProgramAccounts`, and the code explicitly acknowledges via comment that the unindexed path has no limit "because we only want to support secondary indexes."

### Recommendation
Extend the byte/size-limit enforcement (`byte_limit_for_scan` + `ScanConfig::recreate_with_abort()`) to the unindexed `load_by_program`/`load_by_program_with_filter` scan path as well, not just the indexed path, so that `get_filtered_program_accounts` always aborts once accumulated result size (or account count) exceeds a configurable bound regardless of whether a secondary index is enabled. Consider a hard, non-optional default cap (rather than requiring the operator to explicitly pass `--accounts-index-scan-results-limit-mb`) for these unindexed scans.

### Proof of Concept
1. Run a validator/RPC node with `--full-rpc-api` and without `--account-index program-id/spl-token-owner/spl-token-mint`, and without `--accounts-index-scan-results-limit-mb`.
2. As an unprivileged actor, create a very large number of low-lamport accounts owned by `system_program::id()` (or use an existing large SPL-Token population and target `getTokenAccountsByDelegate` with a fixed delegate/owner value that many accounts share, which is entirely attacker-choosable data).
3. Issue a single `getProgramAccounts(system_program::id())` (or `getTokenAccountsByOwner`/`getTokenAccountsByDelegate`) RPC call.
4. Observe that `AccountsDb::scan_accounts` walks the full account population with `ScanConfig::default()` (no abort, no byte limit), causing the RPC-serving thread to allocate/serialize an unbounded `Vec<KeyedAccountSharedData>`, exhausting memory/CPU on that node from a single low-rate request — reproducing the same "unbounded loop over attacker-influenced state, no cap, exceeds resource limit" pattern as the Forta `redeem()` report. [9](#0-8)

### Citations

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

**File:** accounts-db/src/accounts.rs (L396-433)
```rust
    pub fn load_by_index_key_with_filter<F: Fn(&AccountSharedData) -> bool>(
        &self,
        ancestors: &Ancestors,
        bank_id: BankId,
        index_key: &IndexKey,
        filter: F,
        byte_limit_for_scan: Option<usize>,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        let sum = AtomicUsize::default();
        let config = ScanConfig::default().recreate_with_abort();
        let mut collector = Vec::new();
        let result = self
            .accounts_db
            .index_scan_accounts(
                ancestors,
                bank_id,
                *index_key,
                |some_account_tuple| {
                    Self::load_while_filtering(&mut collector, some_account_tuple, |account| {
                        let use_account = filter(account);
                        if use_account
                            && Self::accumulate_and_check_scan_result_size(
                                &sum,
                                account,
                                &byte_limit_for_scan,
                            )
                        {
                            // total size of results exceeds size limit, so abort scan
                            config.abort();
                        }
                        use_account
                    });
                },
                &config,
            )
            .map(|_| collector);
        Self::maybe_abort_scan(result, &config)
    }
```

**File:** accounts-db/src/accounts_db.rs (L3259-3346)
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
        );

        // Step 3: Call scan_func on cache-only entries — pubkeys that exist in the cache but not
        // in the accounts index at all.
        for (pubkey, (cached_account, slot)) in cached_versions {
            if config.is_aborted() {
                break;
            }
            scan_func(Some((&pubkey, cached_account.account.clone(), slot)));
        }
```

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

**File:** rpc/src/rpc.rs (L2260-2308)
```rust
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

**File:** rpc/src/rpc.rs (L5936-5961)
```rust
    fn test_rpc_get_program_accounts() {
        let rpc = RpcHandler::start();
        let bank = rpc.working_bank();

        let new_program_id = Pubkey::new_unique();
        let new_program_account_key = Pubkey::new_unique();
        let new_program_account = AccountSharedData::new(42, 0, &new_program_id);
        bank.store_account(&new_program_account_key, &new_program_account);

        let request = create_test_request(
            "getProgramAccounts",
            Some(json!([new_program_id.to_string()])),
        );
        let result: Vec<RpcKeyedAccount> = parse_success_result(rpc.handle_request_sync(request));
        let expected_value = vec![RpcKeyedAccount {
            pubkey: new_program_account_key.to_string(),
            account: encode_ui_account(
                &new_program_account_key,
                &new_program_account,
                UiAccountEncoding::Binary,
                None,
                None,
            ),
        }];
        assert_eq!(result, expected_value);

```

**File:** accounts-db/src/accounts_scan.rs (L27-31)
```rust
#[derive(Debug, Default)]
pub(crate) struct ScanConfig {
    /// checked by the scan. When true, abort scan.
    pub(crate) abort: Option<Arc<AtomicBool>>,
}
```

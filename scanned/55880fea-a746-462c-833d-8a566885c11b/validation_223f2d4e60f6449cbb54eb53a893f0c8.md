## Title
`getProgramAccounts` scan without a secondary index performs no memory-size limit check, unlike the indexed path - unbounded RPC memory DoS ([File: rpc/src/rpc.rs])

### Summary
The external report's core bug class is that a size guard is written in code but is bypassed because the unbounded data is already materialized in memory before the guard runs (`response_bytes.len() > MAX_SIZE` after `res.bytes().await?`). The same broken invariant — "the memory-limiting guard exists on one code path but not on the path actually taken by default" — is reproduced in Agave's RPC `getProgramAccounts`/`getTokenAccountsBy*` handling: the byte-limited scan (`load_by_index_key_with_filter`, which honors `scan_results_limit_bytes`) is only reached when a secondary index (`AccountIndex::ProgramId`/`SplTokenOwner`/etc.) is configured for that key. When no such index is configured — the default configuration — the request falls through to `get_filtered_program_accounts`, which calls `bank.get_filtered_program_accounts` → `Accounts::load_by_program_with_filter`, a function that has **no byte-limit parameter at all** and unconditionally collects every matching account into a `Vec` in memory.

### Finding Description
`JsonRpcRequestProcessor::get_filtered_program_accounts` explicitly branches on whether a secondary index exists for the requested program: [1](#0-0) 

- If `account_indexes.contains(&AccountIndex::ProgramId)`, the request goes through `get_filtered_indexed_accounts`, which threads `self.config.scan_results_limit_bytes` down to `Accounts::load_by_index_key_with_filter`. That function accumulates `calc_scan_result_size` for every matched account and aborts the scan once the accumulated size exceeds `byte_limit_for_scan`: [2](#0-1) 

- If no such index is configured — which is the *default* validator configuration, since secondary indexes must be explicitly opted into via `--account-index` — the code takes the `else` branch: `bank.get_filtered_program_accounts(...)`, annotated by the comment "this path does not need to provide a mb limit because we only want to support secondary indexes." This calls `Accounts::load_by_program_with_filter`, whose signature carries no `byte_limit_for_scan` argument at all and simply pushes every filter-matching account into an unbounded `Vec<KeyedAccountSharedData>`: [3](#0-2) [4](#0-3) 

This exactly mirrors CBST-01's broken invariant: a size-limiting mechanism exists in the codebase (`scan_results_limit_bytes` / `ScanConfig::recreate_with_abort`), but the actual data path taken under normal, default conditions bypasses it entirely, allowing the full, unbounded result set to be materialized in the validator's RPC process memory before any check can occur.

### Impact Explanation
An unprivileged remote RPC client can call `getProgramAccounts` (or `getTokenAccountsByOwner`/`getTokenAccountsByDelegate` without a mint filter routed to the indexed path) against a widely-owned program id (e.g. the System Program, a popular token program, or any program with many large accounts) on a node that has not enabled the corresponding secondary index. Because `load_by_program_with_filter` has no byte budget, the entire matching account set — potentially gigabytes of account data on a fully-loaded mainnet-class validator — is copied into a `Vec` in the RPC thread's heap in a single synchronous call. This can exhaust the process's memory and crash or severely degrade the validator/RPC node with a single, low-rate, unauthenticated request. This matches the explicitly allowed impact category: "single-client low-rate RPC crash/degradation."

### Likelihood Explanation
Likelihood is high for RPC-exposed nodes that have `getProgramAccounts` enabled (part of `full_api`) without secondary indexes configured for the target program — which is the common/default operator configuration, since indexes carry their own indexing overhead and are opt-in. No authentication, stake, or special access is required; a single JSON-RPC POST is sufficient to trigger the unbounded scan and collection.

### Recommendation
Apply the same `scan_results_limit_bytes` accounting used in `load_by_index_key_with_filter` to the non-indexed path in `Accounts::load_by_program_with_filter` (and any other unbounded scan-collector, e.g. `scan_all`), aborting the scan once the accumulated matched-account size exceeds the configured limit, regardless of whether a secondary index is present. Additionally, `JsonRpcRequestProcessor::get_filtered_program_accounts`'s non-indexed branch should thread `self.config.scan_results_limit_bytes` through to `bank.get_filtered_program_accounts`/`load_by_program_with_filter` instead of skipping it.

### Proof of Concept
1. Run a validator/RPC node with default config: `full_api` enabled, no `--account-index program-id` set (the common default).
2. Send `getProgramAccounts` for a program id owning a very large number/volume of accounts (e.g. a heavily used token program or system-owned accounts), with no `dataSlice`/tight filters that would reduce collected data:
```json
{"jsonrpc":"2.0","id":1,"method":"getProgramAccounts","params":["<PROGRAM_ID>"]}
```
3. Observe that `get_filtered_program_accounts` takes the `else` branch (`rpc/src/rpc.rs:2283-2307`), invoking `bank.get_filtered_program_accounts` → `Accounts::load_by_program_with_filter` (`accounts-db/src/accounts.rs:338-358`), which has no `byte_limit_for_scan` and collects unboundedly, unlike the indexed path (`accounts-db/src/accounts.rs:396-433`) that would have aborted per `scan_results_limit_bytes`.
4. Repeated or targeted requests against large account sets can exhaust RPC-node memory, causing crash/degradation from a single low-rate unprivileged client — with no way for the operator to bound it via `scan_results_limit_bytes` unless they also enable the (costly) secondary index.

**Note on confidence**: I was not able to fully trace whether any additional upstream request-size/pagination guard (e.g., `dataSlice` enforcement or a hard cap on number of returned accounts) exists elsewhere in the RPC layer that might mitigate this in practice; my search of `rpc/src/rpc.rs` did not surface a `MAX_GET_PROGRAM_ACCOUNTS`-style constant, but a live Devin session with full-text search across the whole `rpc` crate would be needed to rule this out completely.

### Citations

**File:** rpc/src/rpc.rs (L2262-2308)
```rust
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

**File:** accounts-db/src/accounts.rs (L360-433)
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

**File:** runtime/src/bank.rs (L5121-5132)
```rust
    pub fn get_filtered_program_accounts<F: Fn(&AccountSharedData) -> bool>(
        &self,
        program_id: &Pubkey,
        filter: F,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        self.rc.accounts.load_by_program_with_filter(
            &self.ancestors,
            self.bank_id,
            program_id,
            filter,
        )
    }
```

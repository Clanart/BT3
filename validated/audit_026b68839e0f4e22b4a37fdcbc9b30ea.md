This confirms the claim's technical accuracy: there is no server-side scan-cost limit or rate limit on the unindexed `getProgramAccounts` path anywhere in the RPC config — `rpc_max_multiple_accounts` only bounds `getMultipleAccounts`, and `accounts_index_scan_results_limit_mb` (`scan_results_limit_bytes`) is only wired into the indexed path via `get_filtered_indexed_accounts` [1](#0-0) , never into the unindexed fallback in `get_filtered_program_accounts` [2](#0-1) . This is however a long-standing, explicitly documented-in-code design tradeoff ("this path does not need to provide a mb limit because we only want to support secondary indexes") rather than an injected regression, and is well known operationally in the Solana/Agave ecosystem (`getProgramAccounts` without a configured index is universally documented as an expensive, full-scan RPC call).

Audit Report

## Title
Unbounded full-database scan on unindexed `getProgramAccounts` allows single-client RPC resource exhaustion - ([File: rpc/src/rpc.rs])

## Summary
`JsonRpcRequestProcessor::get_filtered_program_accounts` in `rpc/src/rpc.rs` branches on whether `AccountIndex::ProgramId` is configured; when it is not (the default for most RPC nodes, since secondary indexing is opt-in and memory-expensive), the request falls through to `bank.get_filtered_program_accounts` → `Accounts::load_by_program_with_filter` → `accounts_db.scan_accounts`, which iterates every account in the bank using `ScanConfig::default()` with no byte/size limit, unlike the indexed path which enforces `byte_limit_for_scan`. An unprivileged client can trivially force this unbounded path by supplying any `program_id` not covered by the account index, causing repeated full-bank scans on the RPC node's blocking thread pool with no per-request cost cap anywhere in the JSON-RPC config surface (`rpc_max_multiple_accounts` only bounds `getMultipleAccounts`; `accounts_index_scan_results_limit_mb` only applies to the indexed scan path).

## Finding Description
In `get_filtered_program_accounts`, the `else` branch (taken when `AccountIndex::ProgramId` is not configured or the program is excluded) invokes `bank.get_filtered_program_accounts(&program_id, |account| filters.iter().all(...))` with the comment "this path does not need to provide a mb limit because we only want to support secondary indexes" [2](#0-1) . This calls `Bank::get_filtered_program_accounts`, which forwards to `Accounts::load_by_program_with_filter` [3](#0-2) , which performs `accounts_db.scan_accounts` over the entire account set using `ScanConfig::default()` — no abort flag, no size accounting [4](#0-3) . By contrast, the indexed path threads `byte_limit_for_scan` through `load_by_index_key_with_filter`, which uses an abortable `ScanConfig` and aborts once accumulated result size exceeds the configured limit [5](#0-4) , and this limit is only sourced from `self.config.scan_results_limit_bytes` inside `get_filtered_indexed_accounts` [6](#0-5) . Reviewing the validator's RPC config surface confirms no equivalent guard exists for the unindexed path: `rpc_max_multiple_accounts` only bounds `getMultipleAccounts`, and `accounts_index_scan_results_limit_mb` maps only to `scan_results_limit_bytes`, which is never passed into the unindexed `get_filtered_program_accounts` call [7](#0-6) .

## Impact Explanation
Any unprivileged RPC client can send `getProgramAccounts` with a `program_id` guaranteed to miss the secondary index (or on any node run without `--account-index program-id`, which is the common default) to force a full linear scan and per-account filter evaluation across the entire account set, with no server-imposed cost bound. This runs on the RPC node's blocking-task pool (`spawn_blocking`) and is repeatable at low request rates, matching the "single-client low-rate RPC crash/degradation" impact category — it degrades the targeted RPC node without affecting consensus, funds, or other validators.

## Likelihood Explanation
High for any RPC deployment not indexing the exact `program_id` requested. No privilege, trusted plugin, or malicious peer is required; the input (`program_id`, `filters`) is fully attacker-controlled and public. Filter validation (`verify_filters`/`optimize_filters`) checks only syntax, not scan cost, and does not require an index to exist [8](#0-7) .

## Recommendation
- Thread a `byte_limit_for_scan`-style cap (or a new dedicated config, e.g. reusing `scan_results_limit_bytes`) into the unindexed fallback branch of `get_filtered_program_accounts`, using an abortable `ScanConfig` as done for `load_by_index_key_with_filter`.
- Consider gating unindexed `getProgramAccounts` calls behind an explicit opt-in flag, or rejecting/rate-limiting them per-connection when no matching secondary index exists.
- Add server-side scan-cost accounting per RPC connection/IP independent of per-call limits.

## Proof of Concept
1. Run an RPC node without `--account-index program-id` (or query a `program_id` outside the configured index's include set).
2. Send `{"jsonrpc":"2.0","id":1,"method":"getProgramAccounts","params":["<any unindexed pubkey>"]}` repeatedly.
3. Each call enters the `else` branch at `rpc/src/rpc.rs:2283-2301`, triggering `Accounts::load_by_program_with_filter` → `accounts_db.scan_accounts` with `ScanConfig::default()` (no limit), unlike the indexed path.
4. Repeating this from a single low-rate client sustains full-account-set scans with no server-side circuit breaker, degrading RPC responsiveness for other clients.

### Citations

**File:** rpc/src/rpc.rs (L309-341)
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
```

**File:** rpc/src/rpc.rs (L2283-2301)
```rust
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
```

**File:** rpc/src/rpc.rs (L2460-2487)
```rust
pub(crate) fn optimize_filters(filters: &mut [RpcFilterType]) {
    filters.iter_mut().for_each(|filter_type| {
        if let RpcFilterType::Memcmp(compare) = filter_type
            && let Err(err) = compare.convert_to_raw_bytes()
        {
            // All filters should have been previously verified
            warn!("Invalid filter: bytes could not be decoded, {err}");
        }
    })
}

pub(crate) fn verify_filters(filters: &[RpcFilterType]) -> Result<()> {
    if filters.len() > MAX_GET_PROGRAM_ACCOUNT_FILTERS {
        return Err(Error::invalid_params(format!(
            "Too many filters provided; max {MAX_GET_PROGRAM_ACCOUNT_FILTERS}"
        )));
    }
    for filter in filters {
        verify_filter(filter)?;
    }
    Ok(())
}

fn verify_filter(input: &RpcFilterType) -> Result<()> {
    input
        .verify()
        .map_err(|e| Error::invalid_params(format!("Invalid param: {e:?}")))
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

**File:** validator/src/commands/run/args/json_rpc_config.rs (L50-67)
```rust
            max_multiple_accounts: Some(value_t!(matches, "rpc_max_multiple_accounts", usize)?),
            account_indexes: AccountSecondaryIndexes::from_clap_arg_match(matches)?,
            rpc_threads: value_t!(matches, "rpc_threads", usize)?,
            rpc_blocking_threads: value_t!(matches, "rpc_blocking_threads", usize)?,
            rpc_niceness_adj: value_t!(matches, "rpc_niceness_adj", i8)?,
            full_api: matches.is_present("full_rpc_api"),
            rpc_scan_and_fix_roots: matches.is_present("rpc_scan_and_fix_roots"),
            max_request_body_size: Some(value_t!(matches, "rpc_max_request_body_size", usize)?),
            scan_results_limit_bytes: value_t!(
                matches,
                "accounts_index_scan_results_limit_mb",
                usize
            )
            .ok()
            .map(|mb| mb * MB),
            disable_health_check: false,
        })
    }
```

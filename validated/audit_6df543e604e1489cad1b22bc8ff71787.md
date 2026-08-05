Based on the code I've verified, the claim is accurate: the unindexed `getProgramAccounts` path in `rpc/src/rpc.rs` genuinely lacks the `byte_limit_for_scan` mechanism applied only to the indexed path, and delegates to an unbounded `ScanConfig::default()` scan over all accounts.Audit Report

## Title
Unbounded full-database scan on unindexed `getProgramAccounts` allows single-client RPC resource exhaustion - ([File: rpc/src/rpc.rs])

## Summary
`JsonRpcRequestProcessor::get_filtered_program_accounts` in `rpc/src/rpc.rs` only applies a scan-size limit (`byte_limit_for_scan`) when the requested `program_id` is covered by the `AccountIndex::ProgramId` secondary index; when it is not (the default configuration for most validators, since secondary indexing is opt-in), the code falls through to `bank.get_filtered_program_accounts`, which performs a full unbounded scan of every account in the bank via `Accounts::load_by_program_with_filter` using `ScanConfig::default()` with no abort/size limit. An unprivileged client can trivially choose a `program_id` that is guaranteed not to be indexed (or run against any node without `--account-index program-id`) to force this expensive unbounded scan on every call.

## Finding Description
The branch in `get_filtered_program_accounts` checks `self.config.account_indexes.contains(&AccountIndex::ProgramId)` [1](#0-0) . When true, it calls `get_filtered_indexed_accounts`, which passes `self.config.scan_results_limit_bytes` down to `bank.get_filtered_indexed_accounts` → `Accounts::load_by_index_key_with_filter`, which tracks accumulated result size via `accumulate_and_check_scan_result_size` and aborts the scan (`config.abort()`) once the byte limit is exceeded [2](#0-1) [3](#0-2) .

When the index is not present for the given `program_id`, the `else` branch explicitly states "this path does not need to provide a mb limit because we only want to support secondary indexes" and calls `bank.get_filtered_program_accounts` with only a per-account boolean filter, no byte limit [4](#0-3) . `Bank::get_filtered_program_accounts` forwards directly to `Accounts::load_by_program_with_filter` [5](#0-4) , which calls `accounts_db.scan_accounts` over the entire account set using `ScanConfig::default()` — a config with no abort mechanism configured, unlike the `recreate_with_abort()` config used on the indexed path [6](#0-5) .

`verify_filters`/`optimize_filters` only validate filter syntax (count via `MAX_GET_PROGRAM_ACCOUNT_FILTERS`, memcmp byte decoding) — they impose no bound on the amount of scanning work and do not require an index to exist [7](#0-6) . I found no additional per-request cost accounting, semaphore, or rate limiter in `rpc/src/rpc_service.rs` that specifically bounds unindexed `getProgramAccounts` scan cost; the existing guards (index presence check, `scan_results_limit_bytes`) are structurally insufficient because they simply don't apply to this code path.

## Impact Explanation
Any unprivileged RPC client can repeatedly call `getProgramAccounts` with a `program_id` guaranteed to bypass the secondary index (trivial to pick, or unconditionally true on nodes without `--account-index program-id` configured), forcing a full linear scan and per-account filter evaluation over the entire account set on the RPC node's blocking thread pool (`spawn_blocking`) with no server-side scan-size cap. This matches the allowed "single-client low-rate RPC crash/degradation" impact category — it degrades the targeted RPC node's responsiveness/CPU without requiring privileged access, malicious peers, or trusted plugins.

## Likelihood Explanation
High for any deployment lacking `--account-index program-id` (the common/default operational posture, since secondary indexing is memory-expensive and opt-in), or for any `program_id` outside the configured index's include set. The exploit requires no special privilege — only a standard public JSON-RPC `getProgramAccounts` call with an arbitrary/unindexed pubkey — and is fully repeatable at will by a single client.

## Recommendation
- Apply the same `byte_limit_for_scan` / abortable `ScanConfig` mechanism used in `get_filtered_indexed_accounts` (`rpc/src/rpc.rs` line 320, `accounts-db/src/accounts.rs` lines 396-433) to the unindexed fallback path in `get_filtered_program_accounts` (`rpc/src/rpc.rs` lines 2283-2301), instead of relying on the assumption that operators will avoid triggering it.
- Require the account index (or an explicit opt-in configuration) for `getProgramAccounts` calls that would otherwise trigger a full unindexed scan, rejecting or rate-limiting such calls by default.
- Add server-side accounting of cumulative scan cost per RPC connection/IP to bound total unindexed-scan work over time, independent of any single-call limit.

## Proof of Concept
1. Start a validator/RPC node without `--account-index program-id` (or with an index that does not cover the target program).
2. Send repeated requests: `POST / {"jsonrpc":"2.0","id":1,"method":"getProgramAccounts","params":["<any pubkey not in the account_index include set>"]}`.
3. Each call enters the `else` branch of `get_filtered_program_accounts` at `rpc/src/rpc.rs:2283-2301`, invoking `bank.get_filtered_program_accounts` → `Accounts::load_by_program_with_filter` → `accounts_db.scan_accounts` over the full account set with `ScanConfig::default()` (no size/byte limit), unlike the indexed path which enforces `byte_limit_for_scan`.
4. Repeating this at low rate from a single client causes sustained full-database scans on the RPC node's blocking-task pool with no server-side circuit breaker, degrading RPC responsiveness for other clients — reproducible via a Rust integration test that measures scan latency/CPU for repeated unindexed `getProgramAccounts` calls against a bank populated with a large number of accounts.

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

**File:** rpc/src/rpc.rs (L2262-2266)
```rust
        if self
            .config
            .account_indexes
            .contains(&AccountIndex::ProgramId)
        {
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

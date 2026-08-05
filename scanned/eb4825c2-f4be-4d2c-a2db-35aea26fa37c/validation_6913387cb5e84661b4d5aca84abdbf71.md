## Title
Unbounded, unmetered `getProgramAccounts` scan on non-indexed program IDs allows single-client RPC exhaustion/DoS - ([File: rpc/src/rpc.rs])

## Summary
`JsonRpcRequestProcessor::get_filtered_program_accounts` takes two code paths depending on whether `AccountIndex::ProgramId` is enabled. The secondary-index path enforces `scan_results_limit_bytes` via `accumulate_and_check_scan_result_size`/`ScanConfig::recreate_with_abort`, but the default (non-indexed) path — which is what most validators run with, since secondary indexes are opt-in and memory-expensive — has **no size limit, no time limit, and no request-level throttling** at all.

## Finding Description
In the `else` branch of `get_filtered_program_accounts`, the code explicitly documents the omission: [1](#0-0) 

```rust
} else {
    // this path does not need to provide a mb limit because we only want to support secondary indexes
    let mut accounts = self
        .runtime
        .spawn_blocking(move || {
            bank.get_filtered_program_accounts(
                &program_id,
                |account: &AccountSharedData| { ... },
            )
            ...
        })
        .await
        .expect("Failed to spawn blocking task")?;
```

This calls `Bank::get_filtered_program_accounts` → `Accounts::load_by_program_with_filter` [2](#0-1) , which iterates the **entire accounts scan** (`accounts_db.scan_accounts`) with no byte budget, collecting every matching account into a `Vec<KeyedAccountSharedData>` in memory before returning.

Compare this to the indexed path, `get_filtered_indexed_accounts`, which explicitly tracks accumulated bytes and aborts the scan once `scan_results_limit_bytes` is exceeded: [3](#0-2) . The non-indexed path has no equivalent guard, confirmed by the inline comment stating the byte limit is intentionally skipped: [4](#0-3) .

The broken invariant: **every unprivileged `getProgramAccounts` RPC call performs a full, unbounded accounts-db scan and materializes an unbounded result set in the blocking-thread runtime**, with only a fixed-size `rpc_blocking_threads` pool to serve them (`JsonRpcConfig` sets a bounded thread count, e.g. via `rpc_niceness_adj`/`rpc_threads`/`rpc_blocking_threads` seen in `rpc_service.rs`). This mirrors the `pg_sleep`-style DoS in the report: a single cheap, unprivileged, unauthenticated call (no signature or fee required, unlike a transaction) can be repeated at a high rate to tie up all available blocking worker threads and consume large amounts of heap memory, degrading or crashing the RPC service for legitimate clients — matching the accepted "single-client low-rate RPC crash/degradation" impact class.

Existing mitigations do not stop this path:
- `optimize_filters`/`verify_filters` only validate filter syntax, not result cardinality [5](#0-4) .
- `scan_results_limit_bytes` config (`JsonRpcConfig`) is only consulted in the indexed branch [6](#0-5)  — it is never passed into the non-indexed `bank.get_filtered_program_accounts` call.
- No per-IP/per-client rate limiting was found on the JSON-RPC HTTP server construction (`ServerBuilder` in `rpc_service.rs` only configures thread count, CORS, and `max_request_body_size`) [7](#0-6) .

## Impact Explanation
A validator running with default configuration (no secondary indexes, which is the common default) exposes an RPC endpoint that lets any unauthenticated client force a full accounts-db scan and unbounded in-memory result materialization for widely-owned program IDs (e.g. the System Program, Token Program), with no cost/fee gating comparable to a transaction. Repeated/concurrent invocation exhausts the RPC blocking-thread pool and process memory, denying service to legitimate RPC users — a non-RPC-excluded, "single-client low-rate RPC crash/degradation" scenario explicitly within the accepted impact class.

## Likelihood Explanation
High: `getProgramAccounts` is a standard, widely-used, unauthenticated JSON-RPC method requiring only a valid pubkey string; no stake, fee, or signature is needed to invoke it. The vulnerable code path is the *default* configuration path (non-indexed), so it affects the majority of Agave RPC deployments. The comment in the code itself acknowledges the missing limit, confirming this is a real gap rather than a defense-in-depth detail.

## Recommendation
Apply the same `scan_results_limit_bytes` accounting/abort mechanism used in `load_by_index_key_with_filter` (`accumulate_and_check_scan_result_size` + `ScanConfig::recreate_with_abort`) to the non-indexed `load_by_program_with_filter`/`scan_accounts` path in `accounts-db/src/accounts.rs`, and/or add per-connection or global rate limiting for the `getProgramAccounts`/`AccountsScan` RPC methods at the `rpc_service.rs` HTTP server layer, analogous to the parameterized-query fix in the source report (bound the "expensive operation" regardless of caller-supplied parameters).

## Proof of Concept
1. Run a validator with default `JsonRpcConfig` (no `--account-index program-id`).
2. Issue concurrent `getProgramAccounts` requests for a program ID owning a very large number of accounts (e.g. System Program on a busy cluster) at a rate the RPC server does not throttle.
3. Each request occupies a `spawn_blocking` worker for the duration of the full unbounded scan (`rpc/src/rpc.rs:2285-2301`) and allocates memory proportional to the entire matching account set (`accounts-db/src/accounts.rs:338-358`), with no `scan_results_limit_bytes` enforcement in this branch.
4. Repeating this beyond the size of `rpc_blocking_threads` starves the blocking pool, causing legitimate RPC requests (including this same method and others relying on `spawn_blocking`) to queue/hang — a denial of service directly analogous to stacking `pg_sleep()` calls against an unthrottled database connection pool.

**Note on completeness:** I was unable to fully confirm the exact default value of `rpc_blocking_threads`/pool sizing or whether any additional external reverse-proxy/rate-limiting is assumed at deployment time, since that configuration lives outside the indexed source I could inspect in this session (`validator/src/commands/run/args/json_rpc_config.rs` matches were found but not read in full). If further precision on default thread-pool sizing is needed, that file should be reviewed directly.

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

**File:** rpc/src/rpc.rs (L3427-3446)
```rust
        ) -> BoxFuture<Result<OptionalContext<Vec<RpcKeyedAccount>>>> {
            debug!("get_program_accounts rpc request received: {program_id_str:?}");
            async move {
                let program_id = verify_pubkey(&program_id_str)?;
                let (config, filters, with_context, sort_results) = if let Some(config) = config {
                    (
                        Some(config.account_config),
                        config.filters.unwrap_or_default(),
                        config.with_context.unwrap_or_default(),
                        config.sort_results.unwrap_or(true),
                    )
                } else {
                    (None, vec![], false, true)
                };
                verify_filters(&filters)?;
                meta.get_program_accounts(program_id, config, filters, with_context, sort_results)
                    .await
            }
            .boxed()
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

**File:** accounts-db/src/accounts.rs (L366-433)
```rust
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

**File:** rpc/src/rpc_service.rs (L724-743)
```rust
                let server = ServerBuilder::with_meta_extractor(
                    io,
                    move |req: &hyper::Request<hyper::Body>| {
                        let xbigtable = req.headers().get("x-bigtable");
                        if xbigtable.is_some_and(|v| v == "disabled") {
                            request_processor.clone_without_bigtable()
                        } else {
                            request_processor.clone()
                        }
                    },
                )
                .event_loop_executor(runtime.handle().clone())
                .threads(1)
                .cors(DomainsValidation::AllowOnly(vec![
                    AccessControlAllowOrigin::Any,
                ]))
                .cors_max_age(86400)
                .request_middleware(request_middleware)
                .max_request_body_size(max_request_body_size)
                .start_http(&rpc_addr);
```

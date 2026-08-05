Audit Report

## Title
Unbounded, unmetered `getProgramAccounts` scan on non-indexed program IDs allows single-client RPC exhaustion/DoS - ([File: rpc/src/rpc.rs])

## Summary
`JsonRpcRequestProcessor::get_filtered_program_accounts` has two code paths: the secondary-index path enforces `scan_results_limit_bytes` via `accumulate_and_check_scan_result_size`/`ScanConfig::recreate_with_abort`, but the default non-indexed path — the configuration most validators run, since `AccountIndex::ProgramId` is opt-in and memory-expensive — performs a full unbounded `accounts_db` scan with no byte limit, no time limit, and no per-request throttling. This is confirmed directly in the code and its own inline comment acknowledging the omission.

## Finding Description
In `rpc/src/rpc.rs`, the `else` branch of `get_filtered_program_accounts` explicitly documents skipping the limit: [1](#0-0) 

This calls `Bank::get_filtered_program_accounts` → `Accounts::load_by_program_with_filter`, which iterates the accounts scan with no byte budget and collects every matching account into an in-memory `Vec`: [2](#0-1) 

By contrast, the indexed path `get_filtered_indexed_accounts` passes `byte_limit_for_scans` through to `load_by_index_key_with_filter`, which tracks accumulated bytes via `accumulate_and_check_scan_result_size` and aborts the scan via `ScanConfig::recreate_with_abort`/`config.abort()` once the limit is exceeded: [3](#0-2) [4](#0-3) 

The non-indexed path has no equivalent guard. The RPC HTTP server itself is configured with a single event-loop thread and a fixed `max_request_body_size`, with no visible per-connection/per-IP rate limiting layer in `rpc_service.rs`: [5](#0-4) 

Existing mitigations (`optimize_filters`, filter validation) only check filter syntax, not result cardinality, and do not bound scan cost: [6](#0-5) 

## Impact Explanation
This matches the accepted "single-client low-rate RPC crash/degradation" impact class: an unauthenticated, unprivileged client can request `getProgramAccounts` for a widely-owned program ID and force a full, unbounded `accounts_db` scan and unbounded in-memory materialization on a `spawn_blocking` worker, with no fee or signature required (unlike a transaction). Repeated calls can starve the bounded blocking-thread pool and consume significant heap memory, degrading service for legitimate RPC clients.

## Likelihood Explanation
High. `getProgramAccounts` is a standard part of the `full_api` (`AccountsScanImpl`), reachable by any client with just a valid pubkey string, and the vulnerable non-indexed branch is the default configuration since secondary indexes are opt-in and memory-costly. The code's own comment ("this path does not need to provide a mb limit because we only want to support secondary indexes") confirms the omission is deliberate for the indexed case only, leaving the default path unguarded.

## Recommendation
Extend the `scan_results_limit_bytes` accounting/abort mechanism (`accumulate_and_check_scan_result_size` + `ScanConfig::recreate_with_abort`) from `load_by_index_key_with_filter` to `load_by_program_with_filter`/`scan_accounts` in `accounts-db/src/accounts.rs`, threading `byte_limit_for_scan` through `Bank::get_filtered_program_accounts` and the non-indexed branch of `get_filtered_program_accounts` in `rpc/src/rpc.rs`. Additionally, consider per-connection/global rate limiting for `getProgramAccounts` and other `AccountsScan` RPC methods at the `rpc_service.rs` HTTP server layer.

## Proof of Concept
1. Run a validator with default `JsonRpcConfig` (no `--account-index program-id`).
2. Issue concurrent `getProgramAccounts` requests for a program ID (e.g., System Program) owning a large number of accounts.
3. Each request occupies a `spawn_blocking` worker for the full unbounded scan duration (`rpc/src/rpc.rs:2283-2301`) and allocates memory proportional to the entire matching account set (`accounts-db/src/accounts.rs:338-358`), with no `scan_results_limit_bytes` enforcement.
4. Repeating beyond the size of the blocking-thread pool starves it, causing legitimate RPC requests relying on `spawn_blocking` to queue or hang.

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

**File:** rpc/src/rpc.rs (L2254-2308)
```rust
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

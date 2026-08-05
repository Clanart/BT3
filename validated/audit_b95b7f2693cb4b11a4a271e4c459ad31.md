## Analysis

I found a direct structural analog to the GovNFT report's "unbounded getter grows without limit" bug class in Agave's `getProgramAccounts`/token-scan RPC methods.

### Title
Unbounded in-memory account scan in `get_filtered_program_accounts` (no secondary index configured) allows single-client RPC memory exhaustion — (File: `rpc/src/rpc.rs`)

### Summary
The external report describes a getter (`govNFTs()`) that accumulates an ever-growing dataset with no bound, causing failure once the underlying set grows large enough. The Agave analog is the default, non-indexed path of `get_filtered_program_accounts`, which scans and collects **all** matching accounts into a single `Vec` in memory with **no size limit enforced**, unlike the indexed path which does enforce `scan_results_limit_bytes`.

### Finding Description
`get_filtered_program_accounts` in `rpc/src/rpc.rs` branches on whether the requested `program_id` has a secondary index configured (`AccountIndex::ProgramId`). When it does, the scan goes through `get_filtered_indexed_accounts`, which enforces `byte_limit_for_scans` (from `JsonRpcConfig::scan_results_limit_bytes`) and aborts if results exceed the limit, as shown by `accumulate_and_check_scan_result_size`/`maybe_abort_scan`. [1](#0-0) 

However, when no secondary index is configured for that `program_id` (the default state — `account_indexes` is empty unless an operator explicitly opts in via CLI flags), the code takes the `else` branch, explicitly commented "this path does not need to provide a mb limit because we only want to support secondary indexes," and calls `bank.get_filtered_program_accounts` with **no byte limit at all**: [2](#0-1) 

This bottoms out in `Accounts::load_by_program_with_filter`, which collects every matching account into an unbounded `Vec<KeyedAccountSharedData>` with no `ScanConfig` abort mechanism (unlike `load_by_index_key_with_filter`, which threads a `byte_limit_for_scan` through `accumulate_and_check_scan_result_size`): [3](#0-2) [4](#0-3) 

The `scan_results_limit_bytes` config option (`accounts-index-scan-results-limit-mb`) has **no default value** — it is `None` unless an operator sets it — and even when set, it is only wired into the *indexed* scan path, never into the default `get_filtered_program_accounts` fallback used by `getProgramAccounts`, `getTokenAccountsByOwner`, and `getTokenAccountsByDelegate` when no matching secondary index exists. [5](#0-4) [6](#0-5) 

This is exactly the pattern in the external report: a data set (here, all accounts owned by a given program) that grows over time and is fetched via a single unbounded getter with no pagination/limit, until the operation becomes prohibitively expensive/fails. In Agave's case "failure" manifests as unbounded heap allocation for the result `Vec` (each entry containing full account data) on the RPC-serving thread.

### Impact Explanation
A single unprivileged RPC client can issue `getProgramAccounts` (or `getTokenAccountsByOwner`/`getTokenAccountsByDelegate` when no index matches) against a program that owns a very large number of accounts, on a node that has not enabled the `--account-index program-id` secondary index (a common configuration, especially since indexing is opt-in and consumes extra memory/disk itself). The unbounded scan will collect all matching accounts (potentially GBs of data on validators tracking heavily-used programs, e.g. SPL Token, System Program) into memory in a single call, causing severe memory pressure, latency spikes, or an OOM crash of the RPC-serving node — a single-client low-rate RPC crash/degradation, which is within the valid impact scope.

### Likelihood Explanation
This does not require an indexed program-id; it is the *default* execution path for any `getProgramAccounts` call that isn't already covered by a secondary index, which is the common case for most programs on a node not configured with `--account-index program-id`. No special privileges are needed — it is a standard, publicly exposed RPC method (part of the `AccountsScan` API, gated only behind `--full-rpc-api`, which many public RPC providers enable). The only asymmetry is that the equivalent indexed-scan code path already has protective machinery (`byte_limit_for_scan`/abort), demonstrating that the unindexed path's total lack of a limit is an inconsistency/oversight rather than an intentional design choice.

### Recommendation
Apply the same `byte_limit_for_scan`/abort mechanism used by `get_filtered_indexed_accounts` to the non-indexed `get_filtered_program_accounts` path (i.e., thread `scan_results_limit_bytes` through `Accounts::load_by_program_with_filter` and `AccountsDb::scan_accounts`, aborting once the accumulated result size exceeds the configured limit), so unfiltered/unindexed large-result scans fail fast instead of allocating unbounded memory.

### Proof of Concept
1. Run a validator/RPC node with `--full-rpc-api` and without `--account-index program-id`.
2. Have a program on-chain that owns a very large number of accounts (e.g., a widely-used token program) — this occurs naturally over time as the report describes ("registry values grow").
3. Send `getProgramAccounts` for that `program_id` with no (or coarse) filters.
4. Observe that `get_filtered_program_accounts`'s unindexed branch performs the scan with no byte-limit abort, unlike the indexed branch, and the RPC thread's memory usage grows unbounded with the full result set, degrading or crashing the node under repeated/large queries. [2](#0-1)

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

**File:** rpc/src/rpc.rs (L2262-2307)
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

**File:** validator/src/commands/run/args/json_rpc_config.rs (L179-187)
```rust
        Arg::with_name("accounts_index_scan_results_limit_mb")
            .long("accounts-index-scan-results-limit-mb")
            .value_name("MEGABYTES")
            .validator(is_parsable::<usize>)
            .takes_value(true)
            .help(
                "How large accumulated results from an accounts index scan can become. If this is \
                 exceeded, the scan aborts.",
            ),
```

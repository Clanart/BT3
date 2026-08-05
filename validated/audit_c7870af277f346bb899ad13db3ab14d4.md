All citations in the claim check out exactly against the current codebase: `JsonRpcRequestProcessor::get_filtered_program_accounts` branches on `AccountIndex::ProgramId` and falls through to `bank.get_filtered_program_accounts` with no byte-limit comment intact. [1](#0-0) 
This forwards to `Accounts::load_by_program_with_filter`, which calls `accounts_db.scan_accounts` with `&ScanConfig::default()` and no abort/size cap, unlike `load_by_index_key_with_filter`, which threads `byte_limit_for_scan` and calls `config.abort()` once the accumulated size exceeds the limit. [2](#0-1) [3](#0-2) 
`Bank::get_filtered_program_accounts` simply forwards to the unbounded `load_by_program_with_filter` path. [4](#0-3) 

Audit Report

## Title
Unbounded full-database scan on unindexed `getProgramAccounts` allows single-client RPC resource exhaustion - ([File: rpc/src/rpc.rs])

## Summary
`JsonRpcRequestProcessor::get_filtered_program_accounts` only enforces a byte/result-size scan limit when `AccountIndex::ProgramId` is configured and covers the requested `program_id`; otherwise it falls to an unbounded, unindexed full-account scan with no size cap. Any unprivileged client can pick a `program_id` guaranteed not to be indexed (or target a node running without secondary indexing) to force a full linear scan of every account on every call, with no per-request cost bound.

## Finding Description
The RPC handler checks whether `AccountIndex::ProgramId` is configured and covers `program_id`; if so it calls `get_filtered_indexed_accounts`, which threads `byte_limit_for_scan` into `Accounts::load_by_index_key_with_filter`, aborting the scan via `config.abort()` once accumulated result size exceeds the limit. If the index is absent (the common default, since secondary indexing is memory-expensive and opt-in), the code instead calls `bank.get_filtered_program_accounts(&program_id, ...)`, which forwards to `Accounts::load_by_program_with_filter`, which calls `accounts_db.scan_accounts` with a bare `ScanConfig::default()` — no abort flag, no size accumulator, no limit. The per-account filter closure (`account.owner() == program_id && filter(account)`) is evaluated over the entire account set regardless of match likelihood. `verify_filters`/`optimize_filters` validate filter syntax only (count and memcmp byte decoding) and impose no bound on scan cost or requirement that an index exist. This is a genuine, code-verified asymmetry between the indexed and unindexed code paths.

## Impact Explanation
Any unprivileged JSON-RPC client can invoke `getProgramAccounts` with an arbitrary/rare `program_id` to force a full unindexed scan and per-account closure evaluation across the entire account set on the RPC node's blocking-task pool (`spawn_blocking`), with no server-side per-request or per-connection cost cap on this path. Repeated low-rate requests from a single client can degrade or exhaust CPU/IO on that RPC node — this matches the "single-client low-rate RPC crash/degradation" impact category. It does not affect consensus, funds, or rooting; it is a targeted RPC-node degradation vector.

## Likelihood Explanation
High for any RPC node not configured with `--account-index program-id` (or for any `program_id` outside the configured index's include set), which is a common operational configuration since secondary indexing is opt-in and memory-expensive. No special privilege, malicious peer, or trusted plugin is required — this is reachable via a standard public JSON-RPC call.

## Recommendation
- Apply the same `byte_limit_for_scan`/abortable `ScanConfig` mechanism used in `get_filtered_indexed_accounts`/`load_by_index_key_with_filter` to the unindexed fallback path in `get_filtered_program_accounts` and `load_by_program_with_filter`, rather than relying on this path being considered low-risk.
- Consider requiring the account index (or an explicit opt-in flag) for `getProgramAccounts` calls that would otherwise trigger a full unindexed scan, rejecting or rate-limiting such calls otherwise.
- Add server-side accounting of scan cost per RPC connection/IP to bound aggregate unindexed-scan work over time.

## Proof of Concept
1. Start a validator/RPC node without `--account-index program-id` (or with an index that does not cover the target program).
2. Send repeated requests:
```
POST /  {"jsonrpc":"2.0","id":1,"method":"getProgramAccounts","params":["<any pubkey not in the account_index include set>"]}
```
3. Each call enters the `else` branch in `get_filtered_program_accounts` (`rpc/src/rpc.rs:2283-2307`), invoking `bank.get_filtered_program_accounts` → `Accounts::load_by_program_with_filter` → `accounts_db.scan_accounts` over the full account set with `ScanConfig::default()` (no size/byte limit), unlike the indexed path which enforces `byte_limit_for_scan`.
4. Repeating this at low rate from a single client causes sustained full-database scans on the RPC node's blocking-task pool with no server-side circuit breaker, degrading RPC responsiveness for other clients.

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

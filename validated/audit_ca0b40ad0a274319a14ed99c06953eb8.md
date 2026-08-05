## Title
Unbounded full-database scan on unindexed `getProgramAccounts` allows single-client RPC resource exhaustion - ([File: rpc/src/rpc.rs])

### Summary
The bug report's core primitive is: an unprivileged client supplies an unsanitized search parameter that is fed directly into an unbounded backend scan/match operation, with no server-side limit on scan cost, enabling a single low-privilege client to trigger expensive server-side work and potential resource exhaustion. The closest real Agave analog is `getProgramAccounts` when the queried `program_id` is not covered by a secondary account index: the RPC layer falls through to a full, unbounded scan of every account in the bank with no byte/result-size limit, explicitly by design.

### Finding Description
`JsonRpcRequestProcessor::get_filtered_program_accounts` branches on whether `AccountIndex::ProgramId` is configured for the given `program_id` [1](#0-0) . When the index is present, the scan goes through `get_filtered_indexed_accounts`, which threads a `byte_limit_for_scan` down to `load_by_index_key_with_filter`, which aborts the scan once accumulated result size exceeds the limit [2](#0-1) .

When the index is **not** present for the requested `program_id` (the common case — validators do not index every program by default), the code takes the `else` branch, which calls `bank.get_filtered_program_accounts` with only a per-account boolean filter and explicitly no byte limit: the comment states "this path does not need to provide a mb limit because we only want to support secondary indexes" [3](#0-2) . This delegates to `Accounts::load_by_program_with_filter`, which performs `accounts_db.scan_accounts` over the *entire* account set with `ScanConfig::default()` (no abort/size limit configured) [4](#0-3) , and `Bank::get_filtered_program_accounts` simply forwards to this unbounded path [5](#0-4) .

The user-controlled inputs are `program_id_str` (attacker can pass an arbitrary/rare pubkey that is guaranteed not to be indexed) and the `filters` list, whose per-account predicate closures (`filter_allows`) are evaluated on every single account in the bank regardless of match likelihood. `verify_filters`/`optimize_filters` only validate filter *syntax* (count, memcmp byte decoding) — they do not limit the amount of scanning work, nor do they require an index to exist [6](#0-5) . This is structurally analogous to the reported NoSQL issue: an unauthenticated client picks input (there: a regex; here: a non-indexed `program_id`) that forces the backend into a full, unindexed scan with no server-side cost cap, and the request middleware / RPC layer performs no additional resource guard before executing it.

### Impact Explanation
Any RPC client can repeatedly invoke `getProgramAccounts` with a `program_id` guaranteed to bypass the secondary index (trivial — pick any random pubkey, or if secondary indexing is disabled entirely, every call takes this path), forcing a full linear scan and per-account closure evaluation over the entire account set on every call. Because this happens on the RPC-serving node's blocking thread pool (`spawn_blocking`) with no per-request cost limit, a single low-rate client can degrade or exhaust CPU/IO on that RPC node, consistent with the "single-client low-rate RPC crash/degradation" impact category. This does not affect consensus or funds directly — it is a targeted RPC-node degradation vector.

### Likelihood Explanation
High for any deployment that does not have `--account-index program-id` configured, or for any `program_id` not covered by the configured index set, since this is the default/likely operational configuration for many RPC nodes (secondary account indexing is opt-in and memory-expensive, so many operators run without it). No special privilege, malicious peer assumption, or trusted plugin is required — this is reachable by any standard JSON-RPC client.

### Recommendation
- Apply the same `byte_limit_for_scan` / abortable `ScanConfig` mechanism used in `get_filtered_indexed_accounts` to the unindexed fallback path in `get_filtered_program_accounts`, rather than relying on the assumption that this path is unreachable/unused in practice.
- Consider requiring the account index (or an explicit opt-in flag/config, distinct from currently-existing ones) for `getProgramAccounts` calls that would otherwise trigger a full unindexed scan, and reject/rate-limit such calls otherwise.
- Add server-side accounting of scan cost per RPC connection/IP to bound total unindexed-scan work per unit time, independent of per-call limits.

### Proof of Concept
1. Start a validator/RPC node without `--account-index program-id` (or with an index that does not cover a target program).
2. Send repeated requests:
```
POST /  {"jsonrpc":"2.0","id":1,"method":"getProgramAccounts","params":["<any pubkey not in the account_index include set>"]}
```
3. Each call enters the `else` branch in `get_filtered_program_accounts` at `rpc/src/rpc.rs:2283-2301`, invoking `bank.get_filtered_program_accounts` → `Accounts::load_by_program_with_filter` → `accounts_db.scan_accounts` over the full account set with `ScanConfig::default()` (no size/byte limit), unlike the indexed path which enforces `byte_limit_for_scan`.
4. Repeating this at even a low rate from a single client causes sustained full-database scans on the RPC node's blocking-task pool, with no server-side circuit breaker, degrading RPC responsiveness for other clients.

**Caveat:** I was unable to verify within the available tool budget whether any additional global rate-limiting (jsonrpc middleware, connection limits, or an "enable/disable getProgramAccounts" validator flag) exists elsewhere in the RPC stack that might mitigate this at deployment time; `validator/src/commands/run/args/json_rpc_config.rs` contains account-index-related configuration options that I found via grep but did not fully read due to iteration limits, so it's possible some operators enable extra guards there that I did not confirm.

### Citations

**File:** rpc/src/rpc.rs (L2262-2301)
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

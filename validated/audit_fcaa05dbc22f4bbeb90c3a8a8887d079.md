Audit Report

## Title
Unauthenticated `/v0/circulating-supply` REST Endpoint Triggers Uncached Full Stake-Account Scan on Every Request - (File: rpc/src/rpc_service.rs)

## Summary
The `/v0/circulating-supply` HTTP REST endpoint is dispatched unconditionally by `RpcRequestMiddleware::on_request` via `match_supply_path`/`process_rest` to `handle_rest`, which calls `calculate_circulating_supply_async` → `calculate_non_circulating_supply`, performing a full, uncached O(accounts) scan of every stake-program account on each request, with no authentication, deduplication, or rate limiting.

## Finding Description
`on_request` matches the request path against `/v0/circulating-supply` or `/v0/total-supply` and unconditionally calls `process_rest` for any matching request, with no auth/session/rate-limit checks present in `RpcRequestMiddleware`. [1](#0-0) 
`process_rest`/`handle_rest` invoke `calculate_circulating_supply_async`, which offloads `calculate_non_circulating_supply(&bank)` onto `tokio::task::spawn_blocking`. [2](#0-1) 
`calculate_non_circulating_supply` scans all stake-program accounts via `get_filtered_indexed_accounts` (or `get_program_accounts` fallback), deserializes each into `StakeStateV2`, and sums balances — with no caching between calls. [3](#0-2) 
By contrast, the RPC server does maintain a `LargestAccountsCache` for the similarly expensive `getLargestAccounts` JSON-RPC method, but no equivalent cache guards this REST path. [4](#0-3) 
Critically, the scan is explicitly isolated to the blocking-thread pool (`rpc_blocking_threads`, default `num_cpus/2`), a design intentionally separate from the async worker threads (`rpc_threads`) that service ordinary JSON-RPC calls, per the code comment in `service_runtime`. [5](#0-4) 
I found no rate-limiting or governor logic anywhere in `rpc/src/rpc_service.rs` guarding this or other REST/RPC paths.

## Impact Explanation
Repeated unauthenticated calls to this endpoint each consume a slot in the shared `rpc_blocking_threads` pool for the duration of a full stake-account scan; if that pool is size-limited and shared with other CPU-bound RPC methods offloaded via `spawn_blocking` (e.g. `getMultipleAccounts`), sustained abuse can degrade or starve those other calls. However, the primary async event-loop threads (`rpc_threads`) that service the bulk of standard JSON-RPC requests are architecturally isolated from this scan by the `spawn_blocking` design, limiting blast radius to blocking-pool-dependent RPC methods rather than the full RPC subsystem, TPU/QUIC/gossip/repair paths, or consensus.

## Likelihood Explanation
The path requires no authentication and no special client capability — any unprivileged client can issue repeated `GET /v0/circulating-supply` requests, and every one of them unconditionally triggers the full uncached scan, matching the "single-client low-rate RPC crash/degradation" impact criterion. Actual severity is contingent on mainnet stake-account count and the configured size of `rpc_blocking_threads`, which is not independently verified here.

## Recommendation
Add a time-based cache (analogous to `LargestAccountsCache`) for `/v0/circulating-supply` and `/v0/total-supply` results, and/or apply per-IP rate limiting on unauthenticated REST endpoints in `RpcRequestMiddleware::on_request`.

## Proof of Concept
Send repeated `GET /v0/circulating-supply` requests to a node with a large stake-account set and measure blocking-thread-pool saturation/latency of other `spawn_blocking`-dependent RPC calls (e.g. `getMultipleAccounts`) under concurrent load, using the code paths at `rpc/src/rpc_service.rs:394-451` and `runtime/src/non_circulating_supply.rs:19-79`.

### Citations

**File:** rpc/src/rpc_service.rs (L394-415)
```rust
        if let Some(path) = match_supply_path(request.uri().path()) {
            process_rest(self.bank_forks.clone(), path)
        } else if self.is_file_get_path(request.uri().path()) {
            self.process_file_get(request.uri().path())
        } else if request.uri().path() == "/health" {
            hyper::Response::builder()
                .status(hyper::StatusCode::OK)
                .body(hyper::Body::from(self.health_check()))
                .unwrap()
                .into()
        } else {
            request.into()
        }
    }
}

fn match_supply_path(path: &str) -> Option<&str> {
    match path {
        "/v0/circulating-supply" | "/v0/total-supply" => Some(path),
        _ => None,
    }
}
```

**File:** rpc/src/rpc_service.rs (L422-451)
```rust
async fn calculate_circulating_supply_async(bank: &Arc<Bank>) -> Result<u64, SupplyCalcError> {
    let total_supply = bank.capitalization();
    let bank = Arc::clone(bank);
    let non_circulating_supply =
        tokio::task::spawn_blocking(move || calculate_non_circulating_supply(&bank))
            .await
            .expect("Failed to spawn blocking task")
            .map_err(|e| SupplyCalcError::Scan(e.to_string()))?;

    Ok(total_supply.saturating_sub(non_circulating_supply.lamports))
}

async fn handle_rest(bank_forks: &RwLock<BankForks>, path: &str) -> Option<String> {
    match path {
        "/v0/circulating-supply" => {
            let bank = bank_forks.read().unwrap().root_bank();
            let supply_result = calculate_circulating_supply_async(&bank).await;
            match supply_result {
                Ok(supply) => Some(build_balance_message(supply, false, false)),
                Err(_) => None,
            }
        }
        "/v0/total-supply" => {
            let bank = bank_forks.read().unwrap().root_bank();
            let total_supply = bank.capitalization();
            Some(build_balance_message(total_supply, false, false))
        }
        _ => None,
    }
}
```

**File:** rpc/src/rpc_service.rs (L608-610)
```rust
        let largest_accounts_cache = Arc::new(RwLock::new(LargestAccountsCache::new(
            LARGEST_ACCOUNTS_CACHE_DURATION,
        )));
```

**File:** rpc/src/rpc_service.rs (L795-827)
```rust
pub fn service_runtime(
    rpc_threads: usize,
    rpc_blocking_threads: usize,
    rpc_niceness_adj: i8,
) -> Arc<TokioRuntime> {
    // The jsonrpc_http_server crate supports two execution models:
    //
    // - By default, it spawns a number of threads - configured with .threads(N) - and runs a
    //   single-threaded futures executor in each thread.
    // - Alternatively when configured with .event_loop_executor(executor) and .threads(1),
    //   it executes all the tasks on the given executor, not spawning any extra internal threads.
    //
    // We use the latter configuration, using a multi threaded tokio runtime as the executor. We
    // do this so we can configure the number of worker threads, the number of blocking threads
    // and then use tokio::task::spawn_blocking() to avoid blocking the worker threads on CPU
    // bound operations like getMultipleAccounts. This results in reduced latency, since fast
    // rpc calls (the majority) are not blocked by slow CPU bound ones.
    //
    // NB: `rpc_blocking_threads` shouldn't be set too high (defaults to num_cpus / 2). Too many
    // (busy) blocking threads could compete with CPU time with other validator threads and
    // negatively impact performance.
    let rpc_threads = 1.max(rpc_threads);
    let rpc_blocking_threads = 1.max(rpc_blocking_threads);
    Arc::new(
        TokioBuilder::new_multi_thread()
            .worker_threads(rpc_threads)
            .max_blocking_threads(rpc_blocking_threads)
            .on_thread_start(move || renice_this_thread(rpc_niceness_adj).unwrap())
            .thread_name("solRpcEl")
            .enable_all()
            .build()
            .expect("Runtime"),
    )
```

**File:** runtime/src/non_circulating_supply.rs (L19-79)
```rust
pub fn calculate_non_circulating_supply(bank: &Bank) -> ScanResult<NonCirculatingSupply> {
    debug!("Updating Bank supply, epoch: {}", bank.epoch());
    let mut non_circulating_accounts_set: HashSet<Pubkey> = HashSet::new();

    for key in non_circulating_accounts() {
        non_circulating_accounts_set.insert(key);
    }
    let withdraw_authority_list = withdraw_authority();

    let clock = bank.clock();
    let stake_accounts = if bank
        .rc
        .accounts
        .accounts_db
        .account_indexes
        .contains(&AccountIndex::ProgramId)
    {
        bank.get_filtered_indexed_accounts(
            &IndexKey::ProgramId(stake::program::id()),
            // The program-id account index checks for Account owner on inclusion. However, due to
            // the current AccountsDb implementation, an account may remain in storage as a
            // zero-lamport Account::Default() after being wiped and reinitialized in later
            // updates. We include the redundant filter here to avoid returning these accounts.
            |account| account.owner() == &stake::program::id(),
            None,
        )?
    } else {
        bank.get_program_accounts(&stake::program::id())?
    };

    for (pubkey, account) in stake_accounts.iter() {
        let stake_account = account
            .deserialize_data::<StakeStateV2>()
            .unwrap_or_default();
        match stake_account {
            StakeStateV2::Initialized(meta)
                if (meta.lockup.is_in_force(&clock, None)
                    || withdraw_authority_list.contains(&meta.authorized.withdrawer)) =>
            {
                non_circulating_accounts_set.insert(*pubkey);
            }
            StakeStateV2::Stake(meta, _stake, _stake_flags)
                if (meta.lockup.is_in_force(&clock, None)
                    || withdraw_authority_list.contains(&meta.authorized.withdrawer)) =>
            {
                non_circulating_accounts_set.insert(*pubkey);
            }
            _ => {}
        }
    }

    let lamports = non_circulating_accounts_set
        .iter()
        .map(|pubkey| bank.get_balance(pubkey))
        .sum();

    Ok(NonCirculatingSupply {
        lamports,
        accounts: non_circulating_accounts_set.into_iter().collect(),
    })
}
```

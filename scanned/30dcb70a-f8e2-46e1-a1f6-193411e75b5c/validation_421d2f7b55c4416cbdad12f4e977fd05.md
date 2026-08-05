### Title
`simulateTransaction` and `sendTransaction` preflight run CPU-bound simulation synchronously on the shared Tokio RPC reactor threads instead of the dedicated blocking pool — ([File: rpc/src/rpc.rs])

### Summary
The JSON‑RPC methods `simulateTransaction` and `sendTransaction` (preflight) execute `Bank::simulate_transaction` synchronously, inline, on the same Tokio worker threads that the whole JSON‑RPC server uses to poll *every* other RPC request. Every other CPU‑bound RPC method in this file (`get_account_info`, `get_multiple_accounts`, `get_filtered_program_accounts`, `get_block`, `get_largest_accounts`, etc.) explicitly offloads its work via `self.runtime.spawn_blocking(...)` to a separate, dedicated blocking‑thread pool for exactly this reason. `simulateTransaction`/`sendTransaction` do not, so an attacker who submits CPI-heavy, compute‑heavy transactions to `simulateTransaction` can occupy the small, fixed pool of RPC reactor worker threads for the full duration of transaction execution, starving all other JSON‑RPC traffic (including unrelated “cheap” calls) served by the same node.

### Finding Description
`simulateTransaction` is declared and implemented as a plain synchronous handler, not `BoxFuture`: [1](#0-0) 

Its implementation directly calls `bank.simulate_transaction(&transaction, enable_cpi_recording)` on the calling thread with no `spawn_blocking` wrapper: [2](#0-1) 

`sendTransaction`'s preflight path has the identical issue — it also calls `preflight_bank.simulate_transaction(&transaction, false)` synchronously before forwarding the transaction: [3](#0-2) 

`Bank::simulate_transaction` itself performs a full `load_and_execute_transactions` pass — loading all referenced/CPI accounts, replenishing the program cache, and running the SBF/BPF VM up to the transaction's compute-unit budget: [4](#0-3) 

Compare this to every other CPU/IO‑bound handler in the same file, which is explicitly wrapped in `self.runtime.spawn_blocking(...)` specifically to avoid consuming the shared reactor threads: [5](#0-4) [6](#0-5) 

The reason this pattern exists is spelled out directly in the comment on `service_runtime`, which builds the single Tokio multi-thread runtime used as the `event_loop_executor` for the whole `jsonrpc_http_server`: [7](#0-6) 

That runtime has exactly `rpc_threads` worker threads (default `num_cpus`) that are shared for *polling every RPC request the node serves* (`.event_loop_executor(runtime.handle().clone()).threads(1)`): [8](#0-7) 

Because `simulateTransaction`/`sendTransaction` do not use `spawn_blocking`, a long-running synchronous VM execution runs directly inside the future being polled by one of these `rpc_threads` reactor threads. Tokio's blocking-thread pool (`max_blocking_threads`, used by `spawn_blocking`) is never engaged for this call, so it provides no isolation here. If an attacker submits `N` concurrent `simulateTransaction` requests (N == number of reactor worker threads, which is bounded and typically small, e.g., `num_cpus`), each carrying a CPI-heavy, near-`MAX_COMPUTE_UNIT_LIMIT` transaction, all reactor threads become occupied executing SVM code and cannot poll/advance *any* other in-flight RPC future — including unrelated, cheap requests like `getHealth`, `getSlot`, `getLatestBlockhash`, and even other clients' `sendTransaction` calls — for the duration of each simulation.

Attacker-controlled levers that directly maximize occupancy time per request (from `RpcSimulateTransactionConfig`) are fully exposed at the entrypoint: [9](#0-8) 
- `data`: attacker-crafted serialized transaction bytes with CPI-heavy program chains, driving CU consumption up to the compute-budget max (`MAX_COMPUTE_UNIT_LIMIT`, `program-runtime/src/execution_budget.rs`).
- `sigVerify` / `replaceRecentBlockhash`: control whether/what preflight checks run before the expensive execution.
- `accounts`: additional post-simulation account fetch/encoding work executed on the same thread after simulation.

This breaks the documented invariant of the RPC layer's own thread model — "fast rpc calls (the majority) are not blocked by slow CPU bound ones" — for exactly the two RPC methods that are most naturally CPU-heavy by design (they exist specifically to execute a transaction).

### Impact Explanation
This is a single-client, low-rate, unprivileged RPC degradation/DoS vector: with a small number of concurrent connections (bounded by, and no larger than, `rpc_threads`, which defaults to `num_cpus` and is often configured lower on constrained nodes), an attacker can stall the JSON‑RPC reactor for as long as it takes to execute `N` compute‑maxed CPI-heavy simulations, denying availability of the JSON-RPC endpoint (including `sendTransaction`) to all other users of that RPC node during that window. This falls into the "single-client low-rate RPC crash/degradation" category explicitly listed as a valid impact. It does not affect consensus, ledger state, or funds — it is a liveness/availability issue scoped to the JSON‑RPC service of the affected node, not the wider cluster.

### Likelihood Explanation
High likelihood of triggerability: `simulateTransaction` is a fully public, unauthenticated JSON-RPC method; no special privileges, stake, or trust relationship are required. Constructing a maximal-CU, CPI-heavy transaction (e.g., invoking a deployed program that recursively CPIs to itself or to `System`/other builtins in a loop up to the max instruction trace length while consuming close to `MAX_COMPUTE_UNIT_LIMIT`) is straightforward and entirely within attacker control (bytes, `sigVerify`, `replaceRecentBlockhash` are all attacker-chosen). The only constraint is opening `rpc_threads` concurrent connections/requests, which is trivial for any client capable of sending a handful of parallel HTTP requests.

### Recommendation
Wrap the CPU-bound simulation work in `simulateTransaction` and in `sendTransaction`'s preflight path in `self.runtime.spawn_blocking(...)`, consistent with every other CPU/IO-bound handler in `rpc/src/rpc.rs` (e.g. `get_account_info`, `get_multiple_accounts`, `get_filtered_program_accounts`). Concretely:
- In `rpc/src/rpc.rs`, change `simulate_transaction`'s trait signature to return `BoxFuture<Result<RpcResponse<RpcSimulateTransactionResult>>>` (mirroring `get_block`, `get_signature_statuses`, etc.) and move the call to `bank.simulate_transaction(...)` into `self.runtime.spawn_blocking(move || ...)`.
- Apply the same change to the `send_transaction` preflight call to `preflight_bank.simulate_transaction(...)`.
- Consider additionally rate-limiting/bounding concurrent in-flight simulations per client or globally, since even on the blocking pool, unbounded concurrent max-CU simulations can still exhaust `rpc_blocking_threads`; a per-request or global concurrency cap for `simulateTransaction`/preflight would further reduce worst-case resource occupancy independent of thread-pool placement.

### Proof of Concept
1. Deploy (or use any existing) a program that performs recursive/looped CPI calls and consumes close to `MAX_COMPUTE_UNIT_LIMIT` compute units (e.g. repeated `System::transfer` CPIs, or a tight compute loop with `set_compute_unit_limit` set to the max).
2. Build `N` (where `N == rpc_threads`, e.g. `num_cpus`) unsigned/garbage-signature transactions embedding that program call, each with `sigVerify: false`, `replaceRecentBlockhash: true`.
3. Fire all `N` `simulateTransaction` JSON-RPC requests concurrently against the target validator's RPC endpoint.
4. While these are in flight, issue a cheap request such as `getHealth` or `getSlot` from a separate connection and measure its latency.
5. Compare against a baseline where the same `N` concurrent requests are cheap no-op `getSlot` calls instead of heavy simulations — the heavy-shape case will show `getHealth`/`getSlot` latency spike for the duration of the simulations (bounded by per-tx compute budget × N), demonstrating that `simulateTransaction` monopolizes the shared reactor thread pool rather than sharing it fairly with other RPC traffic, unlike `getMultipleAccounts`/`getProgramAccounts`, which use `spawn_blocking` and do not exhibit this behavior under the same load pattern.

### Citations

**File:** rpc/src/rpc.rs (L534-560)
```rust
    pub async fn get_account_info(
        &self,
        pubkey: Pubkey,
        config: Option<RpcAccountInfoConfig>,
    ) -> Result<RpcResponse<Option<UiAccount>>> {
        let RpcAccountInfoConfig {
            encoding,
            data_slice,
            commitment,
            min_context_slot,
        } = config.unwrap_or_default();
        let bank = self.get_bank_with_config(RpcContextConfig {
            commitment,
            min_context_slot,
        })?;
        let encoding = encoding.unwrap_or(UiAccountEncoding::Binary);

        let response = self
            .runtime
            .spawn_blocking({
                let bank = Arc::clone(&bank);
                move || get_encoded_account(&bank, &pubkey, encoding, data_slice, None)
            })
            .await
            .expect("rpc: get_encoded_account panicked")?;
        Ok(new_response(&bank, response))
    }
```

**File:** rpc/src/rpc.rs (L2284-2301)
```rust
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

**File:** rpc/src/rpc.rs (L3577-3583)
```rust
        #[rpc(meta, name = "simulateTransaction")]
        fn simulate_transaction(
            &self,
            meta: Self::Metadata,
            data: String,
            config: Option<RpcSimulateTransactionConfig>,
        ) -> Result<RpcResponse<RpcSimulateTransactionResult>>;
```

**File:** rpc/src/rpc.rs (L3923-3950)
```rust
            if !skip_preflight {
                let verification_error = transaction.verify().err();

                if verification_error.is_none() && !meta.config.skip_preflight_health_check {
                    match meta.health.check() {
                        RpcHealthStatus::Ok => (),
                        RpcHealthStatus::Unknown => {
                            inc_new_counter_info!("rpc-send-tx_health-unknown", 1);
                            return Err(RpcCustomError::NodeUnhealthy {
                                num_slots_behind: None,
                            }
                            .into());
                        }
                        RpcHealthStatus::Behind { num_slots } => {
                            inc_new_counter_info!("rpc-send-tx_health-behind", 1);
                            return Err(RpcCustomError::NodeUnhealthy {
                                num_slots_behind: Some(num_slots),
                            }
                            .into());
                        }
                    }
                }

                let simulation_result = if let Some(err) = verification_error {
                    TransactionSimulationResult::new_error(err)
                } else {
                    preflight_bank.simulate_transaction(&transaction, false)
                };
```

**File:** rpc/src/rpc.rs (L4010-4057)
```rust
        fn simulate_transaction(
            &self,
            meta: Self::Metadata,
            data: String,
            config: Option<RpcSimulateTransactionConfig>,
        ) -> Result<RpcResponse<RpcSimulateTransactionResult>> {
            debug!("simulate_transaction rpc request received");
            let RpcSimulateTransactionConfig {
                sig_verify,
                replace_recent_blockhash,
                commitment,
                encoding,
                accounts: config_accounts,
                min_context_slot,
                inner_instructions: enable_cpi_recording,
            } = config.unwrap_or_default();
            let tx_encoding = encoding.unwrap_or(UiTransactionEncoding::Base58);
            let binary_encoding = tx_encoding.into_binary_encoding().ok_or_else(|| {
                Error::invalid_params(format!(
                    "unsupported encoding: {tx_encoding}. Supported encodings: base58, base64"
                ))
            })?;
            let (_, mut unsanitized_tx) =
                decode_and_deserialize::<VersionedTransaction>(data, binary_encoding)?;

            let bank = &*meta.get_bank_with_config(RpcContextConfig {
                commitment,
                min_context_slot,
            })?;
            let mut blockhash: Option<RpcBlockhash> = None;
            if replace_recent_blockhash {
                if sig_verify {
                    return Err(Error::invalid_params(
                        "sigVerify may not be used with replaceRecentBlockhash",
                    ));
                }
                let recent_blockhash = bank.last_blockhash();
                unsanitized_tx
                    .message
                    .set_recent_blockhash(recent_blockhash);
                let last_valid_block_height = bank
                    .get_blockhash_last_valid_block_height(&recent_blockhash)
                    .expect("bank blockhash queue should contain blockhash");
                blockhash.replace(RpcBlockhash {
                    blockhash: recent_blockhash.to_string(),
                    last_valid_block_height,
                });
            }
```

**File:** rpc/src/rpc.rs (L4059-4072)
```rust
            let transaction =
                sanitize_transaction(unsanitized_tx, bank, bank.get_reserved_account_keys())?;

            let verification_error = if sig_verify {
                transaction.verify().err()
            } else {
                None
            };

            let simulation_result = if let Some(err) = verification_error {
                TransactionSimulationResult::new_error(err)
            } else {
                bank.simulate_transaction(&transaction, enable_cpi_recording)
            };
```

**File:** runtime/src/bank.rs (L3822-3862)
```rust
    pub fn simulate_transaction_unchecked(
        &self,
        transaction: &impl TransactionWithMeta,
        enable_cpi_recording: bool,
    ) -> TransactionSimulationResult {
        let account_keys = transaction.account_keys();
        let number_of_accounts = account_keys.len();
        let account_overrides = self.get_account_overrides_for_simulation(&account_keys);
        let batch = self.prepare_unlocked_batch_from_single_tx(transaction);
        let mut timings = ExecuteTimings::default();

        let LoadAndExecuteTransactionsOutput {
            mut processing_results,
            balance_collector,
            ..
        } = self.load_and_execute_transactions(
            &batch,
            // After simulation, transactions will need to be forwarded to the leader
            // for processing. During forwarding, the transaction could expire if the
            // delay is not accounted for.
            self.max_processing_age()
                .saturating_sub(MAX_TRANSACTION_FORWARDING_DELAY),
            &mut timings,
            &mut TransactionErrorMetrics::default(),
            TransactionProcessingConfig {
                account_overrides: Some(&account_overrides),
                check_program_deployment_slot: self.check_program_deployment_slot,
                log_messages_bytes_limit: None,
                limit_to_load_programs: true,
                recording_config: ExecutionRecordingConfig {
                    enable_cpi_recording,
                    enable_log_recording: true,
                    enable_return_data_recording: true,
                    enable_transaction_balance_recording: true,
                },
                drop_on_failure: false,
                all_or_nothing: false,
                strict_nonce_size_check: true,
                drop_noop_transactions: true,
            },
        );
```

**File:** rpc/src/rpc_service.rs (L718-743)
```rust
                let request_middleware = RpcRequestMiddleware::new(
                    ledger_path,
                    snapshot_config,
                    bank_forks,
                    health.clone(),
                );
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

**File:** rpc/src/rpc_service.rs (L795-828)
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
}
```

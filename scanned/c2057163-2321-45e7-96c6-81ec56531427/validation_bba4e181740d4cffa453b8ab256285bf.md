## #Vulnerability Found

### Title
`getSignaturesForAddress` calls blockstore synchronously on the shared RPC async-executor thread, violating the “no blocking work on RPC event-loop threads” invariant - (File: `rpc/src/rpc.rs`)

### Summary
`get_signatures_for_address` calls `self.blockstore.get_confirmed_signatures_for_address2(...)` directly inside its `async fn` body, without wrapping it in `self.runtime.spawn_blocking(...)` as every other blockstore/accounts-scanning RPC method in the same file does. [1](#0-0) 

### Finding Description
The RPC server is intentionally built as a multi-threaded Tokio runtime with a *separate* blocking-thread pool specifically so that CPU/IO-bound blockstore or accounts-scan work never occupies the small pool of async worker threads that services all concurrent RPC requests: [2](#0-1) 

This design intent is explicit in the comment: “we do this so we can configure the number of worker threads... and then use `tokio::task::spawn_blocking()` to avoid blocking the worker threads on CPU bound operations like `getMultipleAccounts`. This results in reduced latency, since fast rpc calls (the majority) are not blocked by slow CPU bound ones.” [3](#0-2) 

Every comparable method that touches the blockstore or performs an accounts scan honors this pattern, e.g. `get_transaction` wraps its blockstore lookup in `spawn_blocking`: [4](#0-3) 

as does `get_account_info`, `get_multiple_accounts`, and `get_filtered_indexed_accounts`: [5](#0-4) [6](#0-5) 

`get_signatures_for_address`, however, calls the blockstore method inline on the async task, meaning it runs synchronously on whichever multi-thread-runtime worker (`rpc_threads`, minimum 1, often a small fixed number) happens to be polling that future: [7](#0-6) 

`get_confirmed_signatures_for_address2` performs a RocksDB `AddressSignatures` column-family scan bounded by attacker-controlled `before`/`until`/`limit`/address parameters. An attacker can supply an address with sparse or no transaction history (or a distant `before`/`until` cursor), forcing the underlying iterator to walk a large slot range before it can determine that `limit` results were satisfied or exhausted, since the scan only terminates on hitting `limit` matches or exhausting the address-signature key range. Because this scan executes directly on a Tokio async worker thread rather than the dedicated blocking-thread pool, that worker cannot make progress on any other future (i.e., other concurrent JSON-RPC requests scheduled to the same worker, including cheap ones like `getHealth`/`getSlot`) for the full duration of the scan.

### Impact Explanation
Because `rpc_threads` is typically a small, fixed number of async workers shared by all inbound RPC connections (`event_loop_executor` model with `.threads(1)` for jsonrpc-http-server and a multi-thread Tokio executor sized by `rpc_threads`), monopolizing even one worker with a long synchronous blockstore scan measurably degrades latency/throughput of unrelated cheap RPC calls being served concurrently on that worker — this is exactly the invariant (“RPC worker time should remain fairly shareable under one-client use”) the `spawn_blocking` pattern elsewhere in the file was designed to protect. A single unauthenticated client, staying within the stated low-rate model, can repeatedly pick addresses/cursors that maximize blockstore scan cost, causing cross-method latency inflation/degradation for other RPC clients — matching the “single-client low-rate RPC crash/degradation” impact category.

### Likelihood Explanation
Likelihood is moderate-to-high: the attacker fully controls `address`, `before`, `until`, and `limit`, requires no privileges, and the only defensive parameter (`limit`) caps the *returned* result count but not necessarily the underlying scan cost when few/no matching signatures exist for the chosen address in the scanned slot range. I was not able to retrieve the full body of `get_confirmed_signatures_for_address2` (only its signature location) to precisely characterize the worst-case scan cost/early-termination behavior, so the exact magnitude of achievable worker-thread occupation is not fully confirmed from the code inspected — I flag this as unverified and recommend a background Devin session read the full `ledger/src/blockstore.rs::get_confirmed_signatures_for_address2` implementation (and its `find_address_signatures_for_slot` helper) to quantify worst-case scan cost precisely.

### Recommendation
Wrap the `self.blockstore.get_confirmed_signatures_for_address2(...)` call in `get_signatures_for_address` with `self.runtime.spawn_blocking(...)`, consistent with `get_transaction`, `get_account_info`, and other blockstore/accounts-scan RPC methods, so that this potentially expensive synchronous RocksDB scan runs on the dedicated blocking-thread pool rather than the shared async worker threads.

### Proof of Concept
Not independently reproduced in this review; would require running the validator against a populated blockstore, issuing `getSignaturesForAddress` for an address with sparse/no history and a distant `before` cursor from one client while measuring latency of a concurrent cheap control call (e.g. `getHealth`) from a second client, as suggested by the question's "Fast validation" hint. This was not executed as part of this static review.

### Citations

**File:** rpc/src/rpc.rs (L321-341)
```rust
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

**File:** rpc/src/rpc.rs (L551-559)
```rust
        let response = self
            .runtime
            .spawn_blocking({
                let bank = Arc::clone(&bank);
                move || get_encoded_account(&bank, &pubkey, encoding, data_slice, None)
            })
            .await
            .expect("rpc: get_encoded_account panicked")?;
        Ok(new_response(&bank, response))
```

**File:** rpc/src/rpc.rs (L1784-1799)
```rust
        let confirmed_transaction = self
            .runtime
            .spawn_blocking({
                let blockstore = Arc::clone(&self.blockstore);
                let confirmed_bank = Arc::clone(&confirmed_bank);
                move || {
                    if commitment.is_confirmed() {
                        let highest_confirmed_slot = confirmed_bank.slot();
                        blockstore.get_complete_transaction(signature, highest_confirmed_slot)
                    } else {
                        blockstore.get_rooted_transaction(signature)
                    }
                }
            })
            .await
            .expect("Failed to spawn blocking task");
```

**File:** rpc/src/rpc.rs (L1847-1886)
```rust
    pub async fn get_signatures_for_address(
        &self,
        address: Pubkey,
        before: Option<Signature>,
        until: Option<Signature>,
        mut limit: usize,
        config: RpcContextConfig,
    ) -> Result<Vec<RpcConfirmedTransactionStatusWithSignature>> {
        self.check_if_transaction_history_enabled()?;

        let commitment = config.commitment.unwrap_or_default();
        check_is_at_least_confirmed(commitment)?;

        let highest_super_majority_root = self
            .block_commitment_cache
            .read()
            .unwrap()
            .highest_super_majority_root();
        let highest_slot = if commitment.is_confirmed() {
            let confirmed_bank = self.get_bank_with_config(config)?;
            confirmed_bank.slot()
        } else {
            let min_context_slot = config.min_context_slot.unwrap_or_default();
            if highest_super_majority_root < min_context_slot {
                return Err(RpcCustomError::MinContextSlotNotReached {
                    context_slot: highest_super_majority_root,
                }
                .into());
            }
            highest_super_majority_root
        };

        let SignatureInfosForAddress {
            infos: mut results,
            found_before,
            found_until,
        } = self
            .blockstore
            .get_confirmed_signatures_for_address2(address, highest_slot, before, until, limit)
            .map_err(|err| Error::invalid_params(format!("{err}")))?;
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

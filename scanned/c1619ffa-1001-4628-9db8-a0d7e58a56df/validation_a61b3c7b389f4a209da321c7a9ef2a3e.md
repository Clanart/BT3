## Finding: `get_blocks` bypasses the blocking-thread-pool isolation used elsewhere in `rpc.rs`

### Summary

`JsonRpcRequestProcessor::get_blocks` (and `get_blocks_with_limit`) call `Blockstore::rooted_slot_iterator` and `Bank::status_cache_ancestors` directly inside the `async fn` body, without routing them through `self.runtime.spawn_blocking(...)`, unlike every other blockstore-touching RPC method in the same file (`get_block`, `get_transaction`, `get_filtered_indexed_accounts`, `calculate_non_circulating_supply`, etc.), which explicitly offload blockstore/CPU-bound work to the dedicated blocking thread pool. [1](#0-0) 

Compare with `get_block`, which wraps the equivalent blockstore call: [2](#0-1) 

and `get_transaction`: [3](#0-2) 

### Finding Description

The RPC service runtime is explicitly designed around the assumption that only a small pool of "worker" (`rpc_threads`) threads execute the async `MetaIoHandler` reactor, and that any CPU/IO-bound blockstore work must be moved to the separate `rpc_blocking_threads` pool via `spawn_blocking`, precisely so that "fast rpc calls (the majority) are not blocked by slow CPU bound ones": [4](#0-3) 

`get_blocks`, however, runs `self.blockstore.rooted_slot_iterator(...)` and, when commitment is `confirmed`, `confirmed_bank.status_cache_ancestors()` synchronously inline on whichever async worker thread is executing that future — not on the blocking pool: [5](#0-4) 

An attacker controls `start_slot`, `end_slot`, and `commitment` and can request a range up to `MAX_GET_CONFIRMED_BLOCKS_RANGE` slots in a single call, which is validated but not reduced: [6](#0-5) 

`get_blocks_with_limit` has the identical pattern (direct, un-offloaded `rooted_slot_iterator` + `status_cache_ancestors` calls, capped only by the same `MAX_GET_CONFIRMED_BLOCKS_RANGE`): [7](#0-6) 

Because the JSON-RPC HTTP server is configured to run all requests on this same small multi-threaded tokio runtime (`rpc_threads`, defaulting to `num_cpus::get()`), a synchronous, non-yielding iteration/scan occupying one of those worker threads for an extended period reduces the number of threads available to service concurrently arriving "cheap" RPC calls (e.g. `getHealth`, `getVersion`, `getSlot`) that are also scheduled on that same reactor pool.

### Impact Explanation

This is a violation of the intended fairness invariant: the codebase's own design comment states blocking/CPU-bound work must not run on the async worker pool to avoid blocking fast RPC calls, yet `get_blocks`/`get_blocks_with_limit` do exactly that. A single low-rate client sending `getBlocks` requests with maximal, sparsely-rooted ranges can occupy one or more of the limited async worker threads for the duration of the blockstore scan, delaying unrelated concurrent RPC requests queued on the same executor — a shared-resource fairness degradation consistent with "single-client low-rate RPC crash/degradation" in scope.

### Likelihood Explanation

The inputs required (`start_slot`, `end_slot`, `commitment`) are fully unprivileged and unauthenticated, and the range cap (`MAX_GET_CONFIRMED_BLOCKS_RANGE`) still permits large scans. However, I was unable to fully inspect the body of `Blockstore::rooted_slot_iterator` in `ledger/src/blockstore.rs` to quantify the actual per-slot cost of the iteration (i.e., whether it is a cheap in-memory/columnar scan or performs per-slot RocksDB reads), so I cannot confirm the exact magnitude of the worker-thread occupation time this produces, nor definitively rule out that `rpc_threads` (default = `num_cpus`) provides enough parallelism to mask the effect in practice. This should be validated empirically (e.g., timing a concurrent cheap RPC call against a `getBlocks` call with a large sparse range) before treating the severity as confirmed.

### Recommendation

Wrap the `blockstore.rooted_slot_iterator(...)` call (and the `status_cache_ancestors()` filtering work) in `get_blocks` and `get_blocks_with_limit` in `self.runtime.spawn_blocking(...)`, consistent with every other blockstore-accessing method in `rpc.rs`, so this work executes on the dedicated blocking thread pool rather than the shared async reactor threads.

### Proof of Concept

Not independently executed — would require running a validator with `rpc_threads` at default/low value, issuing repeated `getBlocks` calls with `start_slot`/`end_slot` spanning near `MAX_GET_CONFIRMED_BLOCKS_RANGE` while concurrently measuring latency of a cheap control call (e.g. `getHealth`) to confirm and quantify the described worker-thread contention, per the "Fast validation" guidance in the question.

### Citations

**File:** rpc/src/rpc.rs (L1343-1351)
```rust
            self.check_blockstore_writes_complete(slot)?;
            let result = self
                .runtime
                .spawn_blocking({
                    let blockstore = Arc::clone(&self.blockstore);
                    move || blockstore.get_rooted_block(slot, true)
                })
                .await
                .expect("Failed to spawn blocking task");
```

**File:** rpc/src/rpc.rs (L1435-1527)
```rust
    pub async fn get_blocks(
        &self,
        start_slot: Slot,
        end_slot: Option<Slot>,
        config: Option<RpcContextConfig>,
    ) -> Result<Vec<Slot>> {
        let config = config.unwrap_or_default();
        let commitment = config.commitment.unwrap_or_default();
        check_is_at_least_confirmed(commitment)?;

        let highest_super_majority_root = self
            .block_commitment_cache
            .read()
            .unwrap()
            .highest_super_majority_root();

        let min_context_slot = config.min_context_slot.unwrap_or_default();
        if commitment.is_finalized() && highest_super_majority_root < min_context_slot {
            return Err(RpcCustomError::MinContextSlotNotReached {
                context_slot: highest_super_majority_root,
            }
            .into());
        }

        let end_slot = min(
            end_slot.unwrap_or_else(|| start_slot.saturating_add(MAX_GET_CONFIRMED_BLOCKS_RANGE)),
            if commitment.is_finalized() {
                highest_super_majority_root
            } else {
                self.get_bank_with_config(config)?.slot()
            },
        );
        if end_slot < start_slot {
            return Ok(vec![]);
        }
        if end_slot - start_slot > MAX_GET_CONFIRMED_BLOCKS_RANGE {
            return Err(Error::invalid_params(format!(
                "Slot range too large; max {MAX_GET_CONFIRMED_BLOCKS_RANGE}"
            )));
        }

        let lowest_blockstore_slot = self
            .blockstore
            .get_first_available_block()
            .unwrap_or_default();
        if start_slot < lowest_blockstore_slot {
            // If the starting slot is lower than what's available in blockstore assume the entire
            // [start_slot..end_slot] can be fetched from BigTable. This range should not ever run
            // into unfinalized confirmed blocks due to MAX_GET_CONFIRMED_BLOCKS_RANGE
            if let Some(bigtable_ledger_storage) = &self.bigtable_ledger_storage {
                return bigtable_ledger_storage
                    .get_confirmed_blocks(start_slot, (end_slot - start_slot) as usize + 1) // increment limit by 1 to ensure returned range is inclusive of both start_slot and end_slot
                    .await
                    .map(|mut bigtable_blocks| {
                        bigtable_blocks.retain(|&slot| slot <= end_slot);
                        bigtable_blocks
                    })
                    .map_err(|_| {
                        Error::invalid_params(
                            "BigTable query failed (maybe timeout due to too large range?)"
                                .to_string(),
                        )
                    });
            }
        }

        // Finalized blocks
        let mut blocks: Vec<_> = self
            .blockstore
            .rooted_slot_iterator(max(start_slot, lowest_blockstore_slot))
            .map_err(|_| Error::internal_error())?
            .filter(|&slot| slot <= end_slot && slot <= highest_super_majority_root)
            .collect();
        let last_element = blocks
            .last()
            .cloned()
            .unwrap_or_else(|| start_slot.saturating_sub(1));

        // Maybe add confirmed blocks
        if commitment.is_confirmed() {
            let confirmed_bank = self.get_bank_with_config(config)?;
            if last_element < end_slot {
                let mut confirmed_blocks = confirmed_bank
                    .status_cache_ancestors()
                    .into_iter()
                    .filter(|&slot| slot <= end_slot && slot > last_element)
                    .collect();
                blocks.append(&mut confirmed_blocks);
            }
        }

        Ok(blocks)
    }
```

**File:** rpc/src/rpc.rs (L1529-1606)
```rust
    pub async fn get_blocks_with_limit(
        &self,
        start_slot: Slot,
        limit: usize,
        config: Option<RpcContextConfig>,
    ) -> Result<Vec<Slot>> {
        let config = config.unwrap_or_default();
        let commitment = config.commitment.unwrap_or_default();
        check_is_at_least_confirmed(commitment)?;

        if limit > MAX_GET_CONFIRMED_BLOCKS_RANGE as usize {
            return Err(Error::invalid_params(format!(
                "Limit too large; max {MAX_GET_CONFIRMED_BLOCKS_RANGE}"
            )));
        }

        let lowest_blockstore_slot = self
            .blockstore
            .get_first_available_block()
            .unwrap_or_default();

        if start_slot < lowest_blockstore_slot {
            // If the starting slot is lower than what's available in blockstore assume the entire
            // range can be fetched from BigTable. This range should not ever run into unfinalized
            // confirmed blocks due to MAX_GET_CONFIRMED_BLOCKS_RANGE
            if let Some(bigtable_ledger_storage) = &self.bigtable_ledger_storage {
                return Ok(bigtable_ledger_storage
                    .get_confirmed_blocks(start_slot, limit)
                    .await
                    .unwrap_or_default());
            }
        }

        let highest_super_majority_root = self
            .block_commitment_cache
            .read()
            .unwrap()
            .highest_super_majority_root();

        if commitment.is_finalized() {
            let min_context_slot = config.min_context_slot.unwrap_or_default();
            if highest_super_majority_root < min_context_slot {
                return Err(RpcCustomError::MinContextSlotNotReached {
                    context_slot: highest_super_majority_root,
                }
                .into());
            }
        }

        // Finalized blocks
        let mut blocks: Vec<_> = self
            .blockstore
            .rooted_slot_iterator(max(start_slot, lowest_blockstore_slot))
            .map_err(|_| Error::internal_error())?
            .take(limit)
            .filter(|&slot| slot <= highest_super_majority_root)
            .collect();

        // Maybe add confirmed blocks
        if commitment.is_confirmed() {
            let confirmed_bank = self.get_bank_with_config(config)?;
            if blocks.len() < limit {
                let last_element = blocks
                    .last()
                    .cloned()
                    .unwrap_or_else(|| start_slot.saturating_sub(1));
                let mut confirmed_blocks = confirmed_bank
                    .status_cache_ancestors()
                    .into_iter()
                    .filter(|&slot| slot > last_element)
                    .collect();
                blocks.append(&mut confirmed_blocks);
                blocks.truncate(limit);
            }
        }

        Ok(blocks)
    }
```

**File:** rpc/src/rpc.rs (L1783-1799)
```rust
        let confirmed_bank = self.bank(Some(CommitmentConfig::confirmed()));
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

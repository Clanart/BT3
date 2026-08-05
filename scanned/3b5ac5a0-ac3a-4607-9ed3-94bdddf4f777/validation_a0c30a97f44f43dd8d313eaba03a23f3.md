Confirmed: `WorkerInfo::shutdown` cancels the worker's `CancellationToken` *before* dropping the sender, and `ConnectionWorker::run` races `main_loop` against `cancel.cancelled()` in a `tokio::select!` — so cancellation can win immediately, terminating the worker while transactions already buffered in its `mpsc` channel (queued but not yet transmitted) are simply dropped, with no drain/flush step. This mirrors the `removeConnectedChain` pattern: an entity (peer connection / worker) is torn down without checking or draining pending queued work, silently losing it. [1](#0-0) [2](#0-1) 

### Title
Silent loss of buffered transactions on QUIC worker eviction/shutdown - (File: tpu-client-next/src/workers_cache.rs, tpu-client-next/src/connection_worker.rs)

### Summary
`WorkersCache` evicts and shuts down a `ConnectionWorker` (LRU eviction in `ensure_worker`/`push`, or forced eviction on `ReceiverDropped` in `try_send_transaction_to_address`/`send_transaction_to_address`, or bulk `flush()`/`shutdown()`) without first draining transactions that are already queued in the worker's `mpsc` channel. Because `WorkerInfo::shutdown()` cancels the worker's `CancellationToken` and then drops the sender, and `ConnectionWorker::run()` selects between processing the channel and observing cancellation, any transaction sitting in the channel but not yet picked up by the `select!` iteration is discarded when cancellation wins the race.

### Finding Description
`WorkersCache` maintains an LRU of `WorkerInfo` entries, each wrapping an `mpsc::Sender<WireTransaction>` feeding a spawned `ConnectionWorker` task. Transactions are pushed onto this channel via `try_send_transaction` / `send_transaction`. [3](#0-2) 

When a worker needs to be replaced (LRU capacity reached in `ensure_worker`/`push`, explicit `pop` after a `ReceiverDropped` error, `flush()` on identity/certificate rotation, or `shutdown()` at scheduler exit), the evicted `WorkerInfo` is wrapped in `ShutdownWorker` and torn down via `shutdown_worker()`, which spawns a task calling `WorkerInfo::shutdown()`. [4](#0-3) [5](#0-4) [6](#0-5) 

`WorkerInfo::shutdown()` cancels the token first, then drops the sender: [1](#0-0) 

Meanwhile, `ConnectionWorker::run()` runs a `tokio::select!` at the top level between `main_loop` (which itself contains the per-message `transaction_receiver.recv()` select) and `cancel.cancelled()`: [7](#0-6) 

Because `cancel.cancel()` is invoked before the sender is dropped, and the outer `select!` in `run()` picks whichever future resolves first, if `cancel.cancelled()` resolves before the worker has drained all queued messages from `transaction_receiver`, the loop exits immediately via the `() = cancel.cancelled() => ()` branch, leaving any transactions still buffered in the channel's queue un-transmitted and permanently discarded (the channel and its buffered contents are dropped along with the task). There is no drain/flush of `transaction_receiver` before exit — the only way `run()` normally learns the queue is empty is by observing `recv() == None` after the sender is dropped, but cancellation short-circuits that entirely.

This is functionally identical to the reported `removeConnectedChain` bug: the connector state (worker/connection) for a "chain"/peer is deleted while messages queued for it are still pending, and no check/flush/drain guards this teardown path.

### Impact Explanation
Transactions accepted into a worker's channel (already counted as "sent to leader" from the scheduler's perspective in `send_to_workers`) can be silently dropped without any error surfaced to the caller of `WorkersCache::push`/`ensure_worker`/`flush`/`shutdown`. Since `tpu-client-next` is used for transaction submission (e.g., for RPC `sendTransaction` fan-out and non-RPC client transaction dispatch paths), this can cause a user's signed transaction to be lost in transit even though the caller (broadcaster) believed the send succeeded, without any error being reported back—an unprivileged transaction-loss condition that undermines the confirmation/retry-decision logic built on top of `SendTransactionStats`.

### Likelihood Explanation
This is a natural race under normal operating conditions, not an attacker-triggered edge case: identity/certificate rotation (`workers.flush()`), LRU eviction under leader churn (`ensure_worker`), and scheduler shutdown (`workers.shutdown()`) are all routine, frequent code paths. Under load with many outstanding worker-cache transactions and frequent leader rotation, the eviction/cancellation race can be hit regularly, making transaction loss plausible in production rather than a purely theoretical bug.

### Recommendation
Before or during shutdown of a `ConnectionWorker`, drain any transactions remaining in `transaction_receiver` (e.g., attempt a final best-effort send, or explicitly count/report them as dropped via `SendTransactionStats` so callers can retry) rather than allowing `cancel.cancelled()` to win the race and discard them silently. Alternatively, close the sender first and let the worker naturally drain the channel to completion (`recv() == None`) before honoring cancellation, and only force-cancel as a last resort for a bounded backstop timeout.

### Proof of Concept
1. Configure a small `worker_channel_size` and enqueue several transactions in quick succession to the same peer via `WorkersCache::send_transaction_to_address`, so multiple `WireTransaction`s are buffered in the `mpsc` channel faster than `ConnectionWorker::send_transaction` can process them (e.g., simulate slow/blocked network I/O in `send_data_over_stream`).
2. Trigger an eviction path concurrently — call `workers.flush()` (as done on identity update in `run_with_broadcaster`) or force LRU eviction via `ensure_worker` for a new peer while capacity is exceeded.
3. Observe that `shutdown_worker` calls `WorkerInfo::shutdown()`, which cancels the token immediately; because `ConnectionWorker::run()`'s outer `select!` can resolve on `cancel.cancelled()` before the inner `main_loop` finishes draining `transaction_receiver`, the still-buffered transactions in the channel are dropped without ever calling `send_data_over_stream`, and no error/stat is recorded for these specific dropped transactions distinguishing them from transactions that failed for network reasons.

Note: I was unable to fully trace how `SendTransactionStats` accounts for transactions dropped in this specific race (versus explicit `ReceiverDropped`/`FullChannel` paths), since instrumentation for this exact "cancelled mid-drain" case was not found in the indexed code; a Devin session with full repository access would be needed to confirm whether any stat increments occur on this path.

### Citations

**File:** tpu-client-next/src/workers_cache.rs (L43-60)
```rust
    fn try_send_transaction(&self, transaction: WireTransaction) -> Result<(), WorkersCacheError> {
        self.sender.try_send(transaction).map_err(|err| match err {
            TrySendError::Full(_) => WorkersCacheError::FullChannel,
            TrySendError::Closed(_) => WorkersCacheError::ReceiverDropped,
        })?;
        Ok(())
    }

    async fn send_transaction(
        &self,
        transaction: WireTransaction,
    ) -> Result<(), WorkersCacheError> {
        self.sender
            .send(transaction)
            .await
            .map_err(|_| WorkersCacheError::ReceiverDropped)?;
        Ok(())
    }
```

**File:** tpu-client-next/src/workers_cache.rs (L62-71)
```rust
    /// Closes the worker by dropping the sender and awaiting the worker's
    /// statistics.
    async fn shutdown(self) -> Result<(), WorkersCacheError> {
        self.cancel.cancel();
        drop(self.sender);
        self.handle
            .await
            .map_err(|_| WorkersCacheError::TaskJoinFailure)?;
        Ok(())
    }
```

**File:** tpu-client-next/src/workers_cache.rs (L151-169)
```rust
    pub fn push(&mut self, leader: SocketAddr, peer_worker: WorkerInfo) -> Option<ShutdownWorker> {
        if let Some((leader, popped_worker)) = self.workers.push(leader, peer_worker) {
            return Some(ShutdownWorker {
                leader,
                worker: popped_worker,
            });
        }
        None
    }

    pub fn pop(&mut self, leader: SocketAddr) -> Option<ShutdownWorker> {
        if let Some(popped_worker) = self.workers.pop(&leader) {
            return Some(ShutdownWorker {
                leader,
                worker: popped_worker,
            });
        }
        None
    }
```

**File:** tpu-client-next/src/workers_cache.rs (L288-297)
```rust
    /// Flushes the cache and asynchronously shuts down all workers. This method
    /// doesn't wait for the completion of all the shutdown tasks.
    pub(crate) fn flush(&mut self) {
        while let Some((peer, current_worker)) = self.workers.pop_lru() {
            shutdown_worker(ShutdownWorker {
                leader: peer,
                worker: current_worker,
            });
        }
    }
```

**File:** tpu-client-next/src/workers_cache.rs (L342-350)
```rust
pub fn shutdown_worker(worker: ShutdownWorker) {
    tokio::spawn(async move {
        let leader = worker.leader();
        let res = worker.shutdown().await;
        if let Err(err) = res {
            debug!("Error while shutting down worker for {leader}: {err}");
        }
    });
}
```

**File:** tpu-client-next/src/connection_worker.rs (L126-190)
```rust
    pub async fn run(&mut self) {
        let cancel = self.cancel.clone();

        let main_loop = async move {
            loop {
                match &self.connection {
                    ConnectionState::Closing => {
                        break;
                    }
                    ConnectionState::NotSetup => {
                        self.create_connection(0).await;
                    }
                    ConnectionState::Active(connection) => {
                        tokio::select! {
                            // Process incoming transactions
                            transaction = self.transaction_receiver.recv() => {
                                match transaction {
                                    Some(transaction) => {
                                        self
                                            .send_transaction(connection.clone(), transaction)
                                            .await;
                                    }
                                    None => {
                                        debug!(
                                            "Transaction sender has been dropped for peer: {}",
                                            self.peer
                                        );
                                        self.connection = ConnectionState::Closing;
                                        continue;
                                    }
                                }
                            }

                            // Monitor connection health proactively
                            close_reason = connection.closed() => {
                                self.handle_connection_closed(close_reason);
                                continue;
                            }
                        }
                    }
                    ConnectionState::Retry(num_reconnects) => {
                        if *num_reconnects > self.max_reconnect_attempts {
                            error!(
                                "Failed to establish connection to {}: reached max reconnect \
                                 attempts",
                                self.peer
                            );
                            self.connection = ConnectionState::Closing;
                            continue;
                        }
                        sleep(RETRY_SLEEP_INTERVAL).await;
                        self.reconnect(*num_reconnects).await;
                    }
                }
            }
        };

        tokio::select! {
            () = main_loop => (),
            () = cancel.cancelled() => (),
        }
        // Cancel it additionally here so that in WorkerInfo we can check if
        // this worker is active.
        cancel.cancel();
    }
```

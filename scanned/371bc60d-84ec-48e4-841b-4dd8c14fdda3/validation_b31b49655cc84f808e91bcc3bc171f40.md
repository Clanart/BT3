Found a genuine analog in `quic-client/src/quic_client.rs`: the `AsyncTaskSemaphore` counter is incremented in `acquire()` on the calling thread but is only decremented by an explicit `release()` call inside the spawned async task, `send_data_async`/`send_data_batch_async` [1](#0-0) . This mirrors the reported bug class exactly: an accounting value is incremented at request time but the code path responsible for "fulfilling" the request is the only place that decrements it, and that decrement is not guaranteed to run.

### Title
`AsyncTaskSemaphore` counter leaked when spawned QUIC send task is dropped without executing `release()` - ([File: quic-client/src/quic_client.rs])

### Summary
`QuicClientConnection::send_data_async` and `send_data_batch_async` call `ASYNC_TASK_SEMAPHORE.acquire()` synchronously (incrementing the shared `counter`), then hand off the actual send/decrement work to a task spawned on the shared Tokio `RUNTIME` via `RUNTIME.spawn(...)` [2](#0-1) . The corresponding `release()` call that decrements `counter` only executes inside the spawned future's body, after the `timeout(...)` future resolves [3](#0-2) . If that spawned task never runs to completion (e.g., the multi-threaded `RUNTIME` is dropped/shut down, or a spawned task is aborted), `release()` is never called and `counter` is permanently over-counted.

### Finding Description
`AsyncTaskSemaphore::acquire()` locks the mutex and does `*count += 1;` immediately, before any task is spawned, and returns the held lock (which is then dropped without touching `count`) [4](#0-3) . The design intent, per the accompanying comment, is: "Before spawning a task, use acquire. After the task is done (be it success or failure), call release" [5](#0-4) .

The invariant "every `acquire()` is matched by exactly one `release()`" is only enforced by the spawned future actually running its body to the `ASYNC_TASK_SEMAPHORE.release();` statement [6](#0-5) , [7](#0-6) . There is no `Drop` guard object returned from `acquire()` that would decrement on early drop — the returned `MutexGuard` intentionally does nothing to the counter on drop (the guard only exists to serialize access while checking/incrementing). Consequently, this is structurally identical to `RedemptionOffer.requested_redemptions`: incremented eagerly on "request" (spawn), but the decrement lives entirely in the "fulfillment" code path (`send_data_async`/`send_data_batch_async` body), with no accounting cleanup if that body is skipped.

Ways the spawned future can be dropped without running its body to completion in this codebase:
- `close_quic_connection` spawns tasks on the same `RUNTIME` and, in the non-async-context branch, calls `RUNTIME.block_on(connection.close())` [8](#0-7) ; if the process is shutting down and the `RUNTIME`'s executor is dropped while `send_data_async`/`send_data_batch_async` tasks are still in-flight, Tokio drops those tasks' futures without running the rest of their bodies — `release()` is skipped, leaking the increment.
- Since `RUNTIME` is a shared, process-wide `LazyLock` (not scoped per-connection), any code path that shuts it down or aborts outstanding `JoinHandle`s (the `_handle` returned by `RUNTIME.spawn(...)` is discarded, so nothing prevents external cancellation/abort) leaves `counter` permanently inflated.

Once `counter` is inflated enough (`> MAX_OUTSTANDING_TASK = 2000`), every subsequent call to `acquire()` — i.e., every subsequent `send_data_async`/`send_data_batch_async` — blocks forever on `self.cond_var.wait(count)` because nothing will ever again call `release()` for the leaked slots [9](#0-8) , [4](#0-3) . This permanently stalls the QUIC client's outbound send path — used by the TPU client to forward transactions to leader nodes — starving transaction submission with no way to self-heal short of process restart.

### Impact Explanation
This does not directly steal funds, but it causes a non-RPC remote-triggerable degradation: any thread calling `send_data_async`/`send_data_batch_async` on the shared QUIC client can be blocked indefinitely once the leaked count crosses `MAX_OUTSTANDING_TASK`. Because the semaphore and runtime are global statics shared by all QUIC connections in the process, a single leak event (e.g., during identity/connection-cache teardown races described in the `close_quic_connection` comment about admin RPC set-identity) can degrade or halt QUIC-based transaction sending cluster-wide for that client, which is a form of non-RPC remote exhaustion/crash within the QUIC/TPU send path.

### Likelihood Explanation
The leak requires the spawned future to be dropped before completing, which is an edge case (runtime shutdown, forced task abort, or dropping an active `JoinHandle` during shutdown races), not a routine operation. This limits likelihood to specific shutdown/reconnect races (e.g., set-identity flows referenced in the code's own comments) rather than an easily attacker-triggered path. I could not fully verify from the available index whether any caller actually aborts these handles or whether `RUNTIME` shutdown is reachable while sends are outstanding in production flows — this warrants confirmation via a full-repo Devin session, since the connection-cache/QUIC endpoint lifecycle code that drives `close_quic_connection` was outside the indexed context returned here.

### Recommendation
Make the accounting symmetric and drop-safe: return an RAII guard from `acquire()` that decrements `counter` and notifies the condition variable in its `Drop` impl, and hold that guard for the full lifetime of the spawned task (e.g., move it into the async block) instead of calling `release()` manually inside the task body. This guarantees the counter is decremented exactly once whether the task completes normally, errors, or is dropped/cancelled before finishing — eliminating the possibility of a permanent counter leak.

### Proof of Concept
Conceptual reproduction (not directly executable from the indexed snippets alone, since the runtime shutdown trigger lives outside this file):
1. Call `send_data_async` (or `send_data_batch_async`) repeatedly; each call runs `ASYNC_TASK_SEMAPHORE.acquire()` then `RUNTIME.spawn(send_data_async(...))`, incrementing `counter` before the task is guaranteed to run [10](#0-9) .
2. Trigger a shutdown/drop of the shared `RUNTIME` (or force-abort the returned `JoinHandle`s) while sends are in flight, so the spawned futures are dropped before reaching `ASYNC_TASK_SEMAPHORE.release();` [6](#0-5) .
3. Observe `counter` never decreases for those in-flight tasks.
4. Repeat until `counter > MAX_OUTSTANDING_TASK` (2000); all subsequent `acquire()` calls block forever in `cond_var.wait`, freezing the QUIC send path for the process [4](#0-3) .

### Citations

**File:** quic-client/src/quic_client.rs (L23-24)
```rust
pub const MAX_OUTSTANDING_TASK: u64 = 2000;
const SEND_DATA_TIMEOUT: Duration = Duration::from_secs(10);
```

**File:** quic-client/src/quic_client.rs (L26-28)
```rust
/// A semaphore used for limiting the number of asynchronous tasks spawn to the
/// runtime. Before spawning a task, use acquire. After the task is done (be it
/// success or failure), call release.
```

**File:** quic-client/src/quic_client.rs (L47-65)
```rust
    /// When returned, the lock has been locked and usage count has been
    /// incremented. When the returned MutexGuard is dropped the lock is dropped
    /// without decrementing the usage count.
    pub fn acquire(&self) -> MutexGuard<'_, u64> {
        let mut count = self.counter.lock().unwrap();
        *count += 1;
        while *count > self.permits {
            count = self.cond_var.wait(count).unwrap();
        }
        count
    }

    /// Acquire the lock and decrement the usage count
    pub fn release(&self) {
        let mut count = self.counter.lock().unwrap();
        *count -= 1;
        self.cond_var.notify_one();
    }
}
```

**File:** quic-client/src/quic_client.rs (L81-103)
```rust
async fn send_data_async(
    connection: Arc<NonblockingQuicConnection>,
    buffer: Arc<Vec<u8>>,
) -> TransportResult<()> {
    let result = timeout(SEND_DATA_TIMEOUT, connection.send_data(&buffer)).await;
    ASYNC_TASK_SEMAPHORE.release();
    handle_send_result(result, connection)
}

async fn send_data_batch_async(
    connection: Arc<NonblockingQuicConnection>,
    buffers: Vec<Vec<u8>>,
) -> TransportResult<()> {
    let result = timeout(
        u32::try_from(buffers.len())
            .map(|size| SEND_DATA_TIMEOUT.saturating_mul(size))
            .unwrap_or(Duration::MAX),
        connection.send_data_batch(&buffers),
    )
    .await;
    ASYNC_TASK_SEMAPHORE.release();
    handle_send_result(result, connection)
}
```

**File:** quic-client/src/quic_client.rs (L163-176)
```rust
    fn send_data_async(&self, data: Arc<Vec<u8>>) -> TransportResult<()> {
        let _lock = ASYNC_TASK_SEMAPHORE.acquire();
        let inner = self.inner.clone();

        let _handle = RUNTIME.spawn(send_data_async(inner, data));
        Ok(())
    }

    fn send_data_batch_async(&self, buffers: Vec<Vec<u8>>) -> TransportResult<()> {
        let _lock = ASYNC_TASK_SEMAPHORE.acquire();
        let inner = self.inner.clone();
        let _handle = RUNTIME.spawn(send_data_batch_async(inner, buffers));
        Ok(())
    }
```

**File:** quic-client/src/quic_client.rs (L184-196)
```rust
pub(crate) fn close_quic_connection(connection: Arc<QuicClient>) {
    // Close the connection and release resources
    trace!("Closing QUIC connection to {}", connection.server_addr());
    // Connection caches can be dropped by async callers, such as admin RPC set-identity;
    // blocking on RUNTIME from a Tokio worker would panic.
    if tokio::runtime::Handle::try_current().is_ok() {
        let _handle = RUNTIME.spawn(async move {
            connection.close().await;
        });
    } else {
        RUNTIME.block_on(connection.close());
    }
}
```

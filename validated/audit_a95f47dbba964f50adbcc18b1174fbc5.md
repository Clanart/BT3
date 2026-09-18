### Title
Send-on-closed-channel panic race between `WorkerPool.Submit`/`SubmitWithMetrics` and `WorkerPool.Close` crashes the public EVM RPC server - ([File: evmrpc/worker_pool.go])

### Summary
`evmrpc/worker_pool.go` implements a check-then-act pattern that mirrors the `rbd` TOCTOU class described in the report: a caller checks a "closed" flag, releases the lock, and only afterward performs the actual queue operation, while a concurrent `Close()` can flip the flag and tear down the underlying channel in between. Unlike the kernel bug (a stale exclusive-lock reacquisition), here the race manifests as a Go runtime panic from sending on a closed channel, which can bring down the RPC-serving goroutine/process.

### Finding Description
`Submit` and `SubmitWithMetrics` first take a read lock, check `wp.closed`, release the lock, and only then attempt `wp.taskQueue <- task` in a `select`: [1](#0-0) 

`Close()` sets `wp.closed = true` under the write lock, then unlocks and closes both `wp.done` and `wp.taskQueue`: [2](#0-1) 

Because the "closed" check and the channel send are two separate critical sections (not one atomic operation), a goroutine calling `Submit`/`SubmitWithMetrics` can observe `closed == false`, release the `RLock`, and then have `Close()` run to completion — including `close(wp.taskQueue)` — before the `select` executes. The subsequent `case wp.taskQueue <- task:` then performs a send on a closed channel, which is not a recoverable condition guarded anywhere in this code path (the `recover()` blocks in `start()` only cover the worker-side execution goroutines, not the caller of `Submit`). This is exactly the TOCTOU pattern the report describes for `lock_dwork`: a cancellation/close operation that is supposed to be authoritative can race with a concurrent operation that "requeues"/re-enters the now-torn-down resource.

### Impact Explanation
A `panic: send on closed channel` is unrecoverable at the point of the send unless the specific call stack that invoked `Submit` has its own `recover()`. If any caller path that calls `wp.Submit`/`SubmitWithMetrics` (used from `evmrpc/filter.go` and `evmrpc/server.go`) does not wrap the call in panic recovery, this crashes the goroutine servicing that RPC request; if that goroutine is not isolated by the HTTP server's own recovery middleware, it can crash the entire `seid` process, meaning any public JSON-RPC node running this worker pool can be brought down by ordinary traffic timed against a pool shutdown/reconfiguration event. This satisfies the "crash of default-configuration RPC nodes" impact bucket in the validation rules.

### Likelihood Explanation
The race window requires `Close()` to be invoked concurrently with in-flight `Submit` calls, which happens during pool shutdown/reinitialization sequences (e.g., server shutdown, or any runtime reconfiguration that closes and re-creates the global pool). Under normal steady-state traffic with no shutdown in progress, the race cannot trigger, since `Close()` is not otherwise called. I could not fully verify from the available code slice whether `Close()` is ever invoked outside of `evmrpc/server.go`'s shutdown path or under what conditions concurrent `Submit` calls are guaranteed to have already stopped before `Close()` runs; a background engineer should check `evmrpc/server.go` for the exact shutdown-ordering guarantees before treating this as unconditionally exploitable at any time.

### Recommendation
Make the "closed" check and the enqueue operation atomic under `wp.mu`, or replace the boolean flag with a design where `Close()` never closes `wp.taskQueue` while `Submit`/`SubmitWithMetrics` might still be racing to send (e.g., always send under `RLock`, and only close the channel after acquiring the write lock and confirming no in-flight readers), or use a `sync/atomic` gate combined with a `recover()` around the send in `Submit`/`SubmitWithMetrics` as defense-in-depth.

### Proof of Concept
1. Create a `WorkerPool` via `NewWorkerPool` and call `Start()`.
2. From goroutine A, call `wp.Submit(task)` repeatedly in a tight loop (simulating concurrent RPC requests hitting `evmrpc/filter.go`).
3. From goroutine B, call `wp.Close()` concurrently.
4. Under `go test -race`, or with sufficient iterations, goroutine A observes `wp.closed == false` in the `RLock` section, then blocks momentarily before executing the `select`; goroutine B completes `Close()` (setting `closed=true`, closing `wp.done`, and closing `wp.taskQueue`) in that window; goroutine A's `select { case wp.taskQueue <- task: ... }` then panics with `send on closed channel`, terminating the calling goroutine unless the caller has its own `recover()`. [3](#0-2)

### Citations

**File:** evmrpc/worker_pool.go (L150-183)
```go
func (wp *WorkerPool) Submit(task func()) error {
	// Check if pool is closed first
	wp.mu.RLock()
	if wp.closed {
		wp.mu.RUnlock()
		return fmt.Errorf("worker pool is closing")
	}
	wp.mu.RUnlock()

	select {
	case wp.taskQueue <- task:
		return nil
	case <-wp.done:
		return fmt.Errorf("worker pool is closing")
	default:
		// Queue is full - fail fast
		return fmt.Errorf("worker pool queue is full")
	}
}

// Close gracefully shuts down the worker pool
func (wp *WorkerPool) Close() {
	wp.mu.Lock()
	if wp.closed {
		wp.mu.Unlock()
		return // Already closed
	}
	wp.closed = true
	wp.mu.Unlock()

	close(wp.done)      // Signal that no new tasks should be submitted.
	close(wp.taskQueue) // Close the queue to signal workers to drain and exit.
	wp.wg.Wait()        // Wait for all workers to finish their remaining tasks.
}
```

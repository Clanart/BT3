### Title
Resource Leak in `TraceChain` via Notifier Cancellation Skips Statedb Release (DoS) - (File: node/cn/tracers/api.go)

### Summary
`UnsafeAPI.TraceChain` (exposed as `debug_traceChain` when the node runs with the `unsafeTrace` flag enabled) spawns a producer goroutine and a pool of worker goroutines to build and trace intermediate `state.StateDB` snapshots for a block range [1](#0-0) . Similar to the OpenFGA `ListObjects` DoS (GHSA-hr4f-6jh8-f2vq), where cancelled calls left resources un-released, a client that opens a `traceChain` subscription and then unsubscribes/disconnects (closing the notifier) causes worker goroutines and the producer goroutine to return early without invoking the `StateReleaseFunc` for state snapshots still queued in the `tasks` channel, leaking pinned trie/database resources.

### Finding Description
The producer goroutine builds a `blockTraceTask` for every block in range, capturing a `release StateReleaseFunc` obtained from `api.backend.StateAtTransaction` [2](#0-1) , and feeds tasks into a buffered channel to be picked up by `threads` worker goroutines [3](#0-2) .

The release for a given task is only added to the shared `releaser` (`reler.add(task.release)`) inside the worker goroutine, and only after the worker has finished executing all transactions in that task [4](#0-3) . Immediately after that, the worker tries to hand the finished task to the `results` channel, racing against `notifier.Closed()`:

```go
if notifier != nil {
    select {
    case results <- task:
    case <-notifier.Closed():
        return
    }
}
``` [5](#0-4) 

Once the subscription's notifier is closed (client unsubscribes or disconnects), this `select` can immediately take the `<-notifier.Closed()` branch and the worker `return`s from the `for task := range tasks` loop entirely — abandoning any additional tasks still buffered in the `tasks` channel. Those abandoned tasks were already constructed with a live `statedb.Copy()` and a captured `release` function, but because the owning worker exits before pulling them from the channel and calling `reler.add(...)`, their `release` callbacks are never registered and thus never invoked by `reler.call()` in the outer defer [6](#0-5) .

The producer goroutine has the same early-return pattern when checking `notifier.Closed()` before constructing the next task [7](#0-6)  and when handing a freshly built task to workers [8](#0-7) , both of which can exit without ensuring the just-built `release` is captured for cleanup.

### Impact Explanation
Each abandoned task pins a `state.StateDB`/trie-node reference that is normally released via `StateReleaseFunc` (deallocating historical state constructed for tracing, per the `StateReleaseFunc` documentation) [9](#0-8) . Repeated `debug_traceChain` subscribe/unsubscribe cycles over ranges of blocks accumulate un-released state references, growing memory/trie-cache usage without bound until the node becomes unresponsive or crashes — a direct denial-of-service analog to the OpenFGA `ListObjects` issue, where repeated calls that get cancelled mid-flight failed to release resources.

### Likelihood Explanation
Exploitation requires only that `debug`/`unsafeTrace` RPC access is available to the caller (a standard public RPC configuration on archive/full nodes that expose tracing) and that the caller can subscribe to `traceChain` over a sufficiently large block range and then cancel the subscription (unsubscribe or drop the connection) while tasks are still in flight — both are trivial, low-cost, repeatable client-side actions requiring no special privilege beyond RPC access.

### Recommendation
Ensure state-release cleanup is decoupled from the `notifier.Closed()` race: register `task.release` with the `releaser` before attempting to forward the task to `results`, or drain and release all remaining buffered tasks in `tasks` inside the deferred cleanup after `pend.Wait()`, so that every constructed `StateReleaseFunc` is guaranteed to run exactly once regardless of when/why the tracing subscription terminates.

### Proof of Concept
1. Run a `kaia` node with `unsafeTrace` enabled (exposing `debug_traceChain`).
2. Open a WebSocket connection and call `debug_traceChain(startBlock, endBlock, config)` for a wide block range so multiple `blockTraceTask`s are buffered concurrently across `threads` worker goroutines.
3. Immediately close the subscription (send `eth_unsubscribe` or drop the WS connection) before workers finish draining the `tasks` channel.
4. Repeat steps 2–3 in a loop; observe that the node's memory/trie-cache usage grows monotonically (via pprof or trie DB size metrics) because `StateReleaseFunc` callbacks for abandoned queued tasks are never invoked, eventually degrading or crashing the node.

### Citations

**File:** node/cn/tracers/api.go (L84-86)
```go
// StateReleaseFunc is used to deallocate resources held by constructing a
// historical state for tracing purposes.
type StateReleaseFunc func()
```

**File:** node/cn/tracers/api.go (L386-402)
```go
func (api *CommonAPI) traceChain(start, end *types.Block, config *TraceConfig, notifier *rpc.Notifier, sub *rpc.Subscription) (map[uint64]*blockTraceResult, error) {
	// Prepare all the states for tracing. Note this procedure can take very
	// long time. Timeout mechanism is necessary.
	reexec := defaultTraceReexec
	if config != nil && config.Reexec != nil {
		reexec = *config.Reexec
	}
	// Execute all the transaction contained within the chain concurrently for each block
	blocks := int(end.NumberU64() - start.NumberU64())
	threads := min(runtime.NumCPU(), blocks)
	var (
		pend     = new(sync.WaitGroup)
		tasks    = make(chan *blockTraceTask, threads)
		results  = make(chan *blockTraceTask, threads)
		localctx = context.Background()
		reler    = new(releaser)
	)
```

**File:** node/cn/tracers/api.go (L430-432)
```go
				}
				// Tracing state is used up, queue it for de-referencing
				reler.add(task.release)
```

**File:** node/cn/tracers/api.go (L434-441)
```go
				// Stream the result back to the result catcher or abort on teardown
				if notifier != nil {
					// Stream the result back to the user or abort on teardown
					select {
					case results <- task:
					case <-notifier.Closed():
						return
					}
```

**File:** node/cn/tracers/api.go (L460-466)
```go
		// Ensure everything is properly cleaned up on any exit path
		defer func() {
			close(tasks)
			pend.Wait()

			// Clean out any pending derefs.
			reler.call()
```

**File:** node/cn/tracers/api.go (L482-489)
```go
			if notifier != nil {
				// Stop tracing if interruption was requested
				select {
				case <-notifier.Closed():
					return
				default:
				}
			}
```

**File:** node/cn/tracers/api.go (L506-515)
```go
			var preferDisk bool
			if statedb != nil {
				s1, s2, s3 := statedb.Database().TrieDB().Size()
				preferDisk = s1+s2+s3 > defaultTracechainMemLimit
			}
			_, _, _, statedb, release, err = api.backend.StateAtTransaction(localctx, next, 0, reexec, statedb, false, preferDisk)
			if err != nil {
				failed = err
				break
			}
```

**File:** node/cn/tracers/api.go (L524-534)
```go
			txs := next.Transactions()
			if notifier != nil {
				select {
				case tasks <- &blockTraceTask{statedb: statedb.Copy(), block: next, release: release, results: make([]*txTraceResult, len(txs))}:
				case <-notifier.Closed():
					return
				}
			} else {
				tasks <- &blockTraceTask{statedb: statedb.Copy(), block: next, release: release, results: make([]*txTraceResult, len(txs))}
			}
			traced += uint64(len(txs))
```

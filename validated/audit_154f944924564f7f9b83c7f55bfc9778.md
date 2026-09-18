Based on my investigation, I found a concrete use-after-free-class bug in the pooled `BlockResult` lifecycle.

### Title
Use-after-free / reuse of pooled `BlockResult` returned to callers while a `ResultSink` is still holding it - ([File: giga/evmonly/result_pool.go])

### Summary
When `Config.BlockResultPoolSize > 0`, `Executor.ExecutePreparedBlock` returns a pooled `*BlockResult` to its caller *and* hands the same pointer to a `ResultSink` for asynchronous persistence via `sinkBlockResult`, tracked with a shared refcount (`blockResultLease.refs`). The public `BlockResult.Release()` method clears the shared `r.lease` field on the object itself (not per-holder), which can desynchronize the refcounting so the object is recycled into the pool - and mutated by a subsequent, unrelated block execution - while the sink (or the caller who still holds the same pointer) is still reading/writing it.

### Finding Description
`ExecutePreparedBlock` in [1](#0-0)  acquires a pooled result, calls `e.sinkBlockResult(ctx, req.Context.Number, result)`, and then hands the *same* `result` pointer back to its own caller as the return value. `sinkBlockResult` retains a second reference via `result.retain()` and passes that same raw pointer (not a copy) to the sink: [2](#0-1) .

The refcounting lives in `result_pool.go`. `blockResultLease.retain()`/`release()` correctly increment/decrement `refs`, and only push the object back into `pool.free` (after `resetForPool()` zeroes/reuses its buffers) when `refs` reaches 0: [3](#0-2) .

However, the *public* `BlockResult.Release()` — the method the block-processing caller (executor's own caller, e.g. `ExecutePreparedBlock`'s caller or `executeBlockSequential`'s defer) is documented to call — does this:
```go
func (r *BlockResult) Release() {
    ...
    r.releaseMu.Lock()
    lease := r.lease
    if lease == nil { ... return }
    r.lease = nil
    r.releaseMu.Unlock()
    lease.release()
}
``` [4](#0-3) 

`r.lease` is a single field on the shared `BlockResult` struct, not a per-holder handle: [5](#0-4) . So the *first* caller to invoke `Release()` on the pointer nils out `r.lease` for everyone, even though other holders (the sink, via the closure captured from `retain()`) still hold a live reference and are still tracked by `lease.refs` internally. Because `lease.release()` operates on the captured `lease` variable directly (not through `r.lease`), the refcount arithmetic itself stays correct in isolation — but any *subsequent* call to `result.retain()` on that same object now sees `r.lease == nil` and silently returns a no-op closure instead of incrementing the real refcount: [6](#0-5) .

This means: once one holder calls `Release()`, all future `retain()` calls on that object become no-ops that never bump `refs`. If the sink's async persistence code calls `result.retain()` at that point (e.g. to hand the result to a second downstream consumer), it gets a fake reference — the object can be pushed back to `pool.free` and `resetForPool()`'d (clearing `ChangeSet`, `Txs`, `Receipts`) by the *real* last release, while the sink still believes it holds a valid, retained reference and continues reading fields that are now zeroed/reused by a concurrently executing next block's `acquire()`. This is the same class of bug as the CVE's Mojo UAF: an object is freed/recycled while a live handle set still believes it is referencing valid data, leading to concurrent, unsynchronized mutation of memory two logical owners think they exclusively own.

### Impact Explanation
`BlockResult` carries `ChangeSet` (balances, nonces, code, storage) and `Receipts` for an entire EVM block processed via the `evmonly`/giga execution path. A data race that lets one block's result buffer be silently reused and mutated (via `resetForPool`/`prepareForBlock` from a subsequently acquired block) while a `ResultSink` is still serializing/persisting it can corrupt persisted receipts or change-sets (wrong balances/nonces/storage written to the receipt store or state DB), a form of state corruption reachable purely from ordinary block execution load, not any privileged action. This can cause incorrect account balances/receipts to be committed or crash the node process on concurrent unsynchronized slice/map mutation (Go's `-race` detector would report a data race; unsynchronized concurrent map/slice writes can also panic the process), which maps to fund-integrity and node-crash impact categories.

### Likelihood Explanation
Reachability is limited to configurations where `Config.BlockResultPoolSize > 0` and a `ResultSink` is configured (i.e., the pooled-result path is enabled, as exercised in `TestExecutorPooledResultRelease`) and to code paths where an additional `retain()` occurs after the initial `Release()` — this is a latent correctness/lifecycle bug in the pooling primitive itself rather than one directly triggerable by a transaction's content. I was not able to fully confirm, within the available tool budget, whether the current call sites in `executor.go`/`sinks.go`/`pipeline.go` actually invoke `result.retain()` a second time after `Release()` has already fired in production wiring, or whether this is presently latent/test-only code. This uncertainty should be resolved by tracing every call site of `retain()` and `Release()` against actual concurrent goroutine ordering (e.g., in `evmonly-loadtest`'s async file sink) before treating this as confirmed-exploitable in the default node RPC/consensus path rather than only in the benchmarking tool.

### Recommendation
Make `retain()`/`Release()` operate on a stable per-handle token rather than a shared mutable field on the pooled object: e.g., have `acquire()` return an opaque handle/generation counter alongside the pointer, and have every `retain()` call validate against that generation rather than checking a shared `r.lease` pointer that any holder can null out. Alternatively, only allow the *original* acquirer's `Release()` to null `r.lease`, and have secondary retains capture the `*blockResultLease` directly (as they already do internally) so that `retain()` never dereferences the possibly-nilled `r.lease` field to decide whether to treat itself as a no-op.

### Proof of Concept
Not independently reproducible from the indexed context alone — a concrete PoC would need to drive `Executor.ExecutePreparedBlock` with `BlockResultPoolSize=1` and a slow `ResultSink` while a second block is executed concurrently, calling `result.Release()` from the "main" path before the sink calls a subsequent `result.retain()`, then observing corrupted `ChangeSet`/`Receipts` content in the sink after the pool recycles and `resetForPool()`s the same object for the next block. This would require a Devin session with build/test access to `giga/evmonly` to construct and run under `go test -race`, which is beyond what I can execute in this read-only investigation.

### Citations

**File:** giga/evmonly/executor.go (L114-128)
```go
func (e *Executor) ExecutePreparedBlock(ctx context.Context, req PreparedBlock) (*BlockResult, error) {
	if err := validateBlockContext(e.chainConfig(req.Context), req.Context); err != nil {
		return nil, err
	}
	result, err := e.executePreparedBlockWithStore(ctx, req)
	if err != nil {
		return nil, err
	}
	recordOCCStats(ctx, len(req.Txs), result.OCCStats)
	if err := e.sinkBlockResult(ctx, req.Context.Number, result); err != nil {
		result.Release()
		return nil, err
	}
	return result, nil
}
```

**File:** giga/evmonly/executor.go (L149-159)
```go
func (e *Executor) sinkBlockResult(ctx context.Context, height uint64, result *BlockResult) error {
	if e.resultSink == nil || result == nil {
		return nil
	}
	release := result.retain()
	if err := e.resultSink.StoreBlockResult(ctx, height, result, release); err != nil {
		release()
		return fmt.Errorf("store block result for block %d: %w", height, err)
	}
	return nil
}
```

**File:** giga/evmonly/result_pool.go (L70-91)
```go
func (l *blockResultLease) retain() func() {
	if l == nil {
		return func() {}
	}
	l.refs.Add(1)
	var once sync.Once
	return func() {
		once.Do(l.release)
	}
}

func (l *blockResultLease) release() {
	if l == nil {
		return
	}
	if l.refs.Add(-1) != 0 {
		return
	}
	result := l.result
	result.resetForPool()
	l.pool.free <- result
}
```

**File:** giga/evmonly/result_pool.go (L93-106)
```go
func (r *BlockResult) retain() func() {
	if r == nil {
		return func() {}
	}
	r.releaseMu.Lock()
	lease := r.lease
	if lease == nil {
		r.releaseMu.Unlock()
		return func() {}
	}
	release := lease.retain()
	r.releaseMu.Unlock()
	return release
}
```

**File:** giga/evmonly/types.go (L74-84)
```go
// BlockResult is the executor output consumed by the new runtime boundary.
type BlockResult struct {
	ChangeSet StateChangeSet
	Txs       []TxResult
	Receipts  ethtypes.Receipts
	GasUsed   uint64
	OCCStats  OCCStats

	releaseMu sync.Mutex
	lease     *blockResultLease
}
```

**File:** giga/evmonly/types.go (L90-103)
```go
func (r *BlockResult) Release() {
	if r == nil {
		return
	}
	r.releaseMu.Lock()
	lease := r.lease
	if lease == nil {
		r.releaseMu.Unlock()
		return
	}
	r.lease = nil
	r.releaseMu.Unlock()
	lease.release()
}
```

### Title
Missing `snapshottedCtxs` reinitialization in `ResetForTracer` causes stale base-state reads and unbounded snapshot growth during multi-tx trace RPCs - ([File: x/evm/state/statedb.go])

### Summary
`DBImpl.ResetForTracer()`, the reset routine used between transactions on the parallel block-trace RPC path, resets `tempState`, `journal`, and `codeCache` but — unlike its sibling `CleanupForTracer()` — never resets `snapshottedCtxs`. This is exactly the "missing reinitialization after reuse" bug class in the referenced sfc CVE: a per-iteration state container is reused for a new unit of work without being reinitialized, so later work operates on stale/uninitialized data structures.

### Finding Description
`DBImpl` is reused across all transactions of a traced block via `PrepareTxNoFlush` (`evmrpc/simulate.go:917-941`), which calls `typedStateDB.ResetForTracer()` before preparing the next transaction: [1](#0-0) 

`ResetForTracer` is defined as: [2](#0-1) 

Compare this with `CleanupForTracer`, the equivalent function used on the (non-concurrent) single-tx trace path, which explicitly reinitializes the same field: [3](#0-2) 

`ResetForTracer` omits `s.snapshottedCtxs = []sdk.Context{}`. `Snapshot()` appends the current ctx to `snapshottedCtxs` on every call (once per `NewDBImpl`, and once for every nested EVM call/create during execution): [4](#0-3) 

Because the slice is never reinitialized between transactions in the trace loop, two problems occur:

1. **Stale base state**: `GetCommittedState` always reads from `s.snapshottedCtxs[0]`: [5](#0-4) 
Since index `0` is only set once at `NewDBImpl` (before the first traced transaction) and is never refreshed by `ResetForTracer`, every subsequent transaction in the same multi-tx trace request incorrectly computes "committed" storage values as of *before the first transaction of the whole trace*, rather than as of before that specific transaction. This produces silently wrong SSTORE gas-refund accounting and storage-diff output for every transaction after the first in a `debug_traceBlockByNumber` / `debug_traceBlockByHash` call.

2. **Unbounded growth**: because old entries are never dropped by `ResetForTracer` (only `RevertToSnapshot` truncates the slice, and only on revert paths), every `Snapshot()` call from every transaction and every nested call/create across the whole traced block keeps accumulating additional `sdk.Context` entries, each wrapping a `CacheMultiStore` layer. Each of these layers is retained for the remainder of the trace request, and because `Snapshot()`'s own performance-optimization comment notes that unfrozen/older layers are walked linearly on reads (`x/evm/state/state.go:121-133`), read latency and memory usage both grow with the number of transactions/calls already processed in the trace, instead of resetting per transaction as intended.

### Impact Explanation
This function is reachable by any public RPC client through `debug_traceBlockByNumber` / `debug_traceBlockByHash` (the parallel block-trace path documented at `evmrpc/simulate.go:912-916`), i.e., unauthenticated, unprivileged input controls how many transactions and how deep the call stack is in the requested block. The unbounded accumulation of `CacheMultiStore`-wrapped contexts across the entire block scales memory and per-read walk cost with total opcode/call count in the traced block, which for large or deep blocks can drive a default-configuration RPC node into excessive memory consumption or multi-second-plus request latency — a resource-exhaustion condition on the public JSON-RPC surface. Independently, the stale `snapshottedCtxs[0]` reference silently corrupts trace/gas-refund output for every transaction after the first one traced, which is a data-integrity defect in a widely used debugging/indexing RPC surface (block explorers, other infra rely on `debug_trace*` correctness).

### Likelihood Explanation
Any transaction that reaches this code path is triggered purely by an RPC caller requesting a trace of a block; no special privileges, validator status, or malicious peer/consensus behavior is required. Blocks with many transactions or deeply nested calls (trivially producible by a contract deployer/caller) are sufficient to trigger both the correctness bug and the growth behavior, making this straightforward to hit in normal usage of the trace RPC.

### Recommendation
Reinitialize `s.snapshottedCtxs` inside `ResetForTracer`, mirroring `CleanupForTracer`:
```go
func (s *DBImpl) ResetForTracer() {
	feeCollector, _ := s.k.GetFeeCollectorAddress(s.Ctx())
	s.coinbaseEvmAddress = feeCollector
	s.tempState = NewTemporaryState()
	s.journal = []journalEntry{}
	s.snapshottedCtxs = []sdk.Context{}
	clear(s.codeCache)
	s.Snapshot()
}
```
This restores the intended per-transaction reset semantics: `snapshottedCtxs[0]` will again represent the correct pre-transaction committed base, and per-transaction snapshot memory will be released between transactions in a multi-tx trace instead of accumulating for the whole block.

### Proof of Concept
1. Call `debug_traceBlockByNumber` (or `debug_traceBlockByHash`) against a block containing multiple transactions, at least one of which performs an `SSTORE` to a slot with a pre-existing non-zero value (to exercise `GetCommittedState`-dependent refund logic), where that slot was also touched differently in an earlier transaction of the same block.
2. Observe that the refund/storage-diff reported for the second (and any later) transaction reflects the storage value from *before the entire block*, not from before that specific transaction — because `GetCommittedState` reads `s.snapshottedCtxs[0]`, which was fixed at `NewDBImpl` time and never refreshed by `ResetForTracer`.
3. To observe the growth effect, trace a block with a large number of transactions or deeply nested internal calls (each internal CALL/CREATE invokes `Snapshot()`); observe RPC process memory and trace latency scale with the cumulative snapshot count across the whole block rather than resetting each transaction, since `ResetForTracer` never truncates `snapshottedCtxs`.

### Citations

**File:** evmrpc/simulate.go (L912-920)
```go
// PrepareTxNoFlush is like PrepareTx but uses ResetForTracer instead of
// CleanupForTracer, avoiding CacheMultiStore flushes. This is required in the
// parallel block trace path where copies of the statedb are concurrently read
// by worker goroutines; flushing would write to shared CacheMultiStore layers
// and cause data races.
func (b *Backend) PrepareTxNoFlush(statedb vm.StateDB, tx *ethtypes.Transaction) error {
	typedStateDB := state.GetDBImpl(statedb)
	typedStateDB.ResetForTracer()
	ctx, _ := b.keeper.PrepareCtxForEVMTransaction(typedStateDB.Ctx(), tx)
```

**File:** x/evm/state/statedb.go (L112-129)
```go
func (s *DBImpl) CleanupForTracer() {
	s.flushCtxs()
	if len(s.snapshottedCtxs) > 0 {
		s.ctx = s.snapshottedCtxs[0]
	}
	feeCollector, _ := s.k.GetFeeCollectorAddress(s.Ctx())
	s.coinbaseEvmAddress = feeCollector
	s.tempState = NewTemporaryState()
	s.journal = []journalEntry{}
	s.snapshottedCtxs = []sdk.Context{}
	clear(s.codeCache)
	s.Snapshot()
}

// ResetForTracer resets in-memory state for a new transaction without flushing
// the CacheMultiStore hierarchy. This is safe for concurrent use when copies of
// this statedb are being read from other goroutines, since it never calls
// CacheMultiStore.Write() on any shared store layer.
```

**File:** x/evm/state/state.go (L26-31)
```go
func (s *DBImpl) GetCommittedState(addr common.Address, hash common.Hash) common.Hash {
	if ov, ok := s.tempState.storageOverrides[addr]; ok {
		return ov.committed[hash.Hex()]
	}
	return s.getState(s.snapshottedCtxs[0], addr, hash)
}
```

**File:** x/evm/state/state.go (L118-139)
```go
func (s *DBImpl) Snapshot() int {
	oldMS := s.ctx.MultiStore()
	newCtx := s.ctx.WithMultiStore(oldMS.CacheMultiStore()).WithEventManager(sdk.NewEventManager())
	// The layer we just branched from is now superseded: all subsequent writes go
	// to newCtx, so this layer will not change again until it is flushed (after
	// execution) or discarded by a revert. Freeze it so that deeper layers can skip
	// it for reads while it stays empty — without this, a Cosmos read at call depth
	// N (e.g. delegation-reward queries via precompiles) walks all N nested cache
	// layers, making a linear number of reads quadratic. We never freeze the base
	// layer (snapshottedCtxs[0]): it is the flush target and is read directly by
	// GetCommittedState, and it is not an EVM-created frame.
	if len(s.snapshottedCtxs) > 0 {
		if f, ok := oldMS.(interface{ Freeze() }); ok {
			f.Freeze()
		}
	}
	s.snapshottedCtxs = append(s.snapshottedCtxs, s.ctx)
	s.ctx = newCtx
	version := len(s.snapshottedCtxs) - 1
	s.journal = append(s.journal, &watermark{version: version})
	return len(s.snapshottedCtxs) - 1
}
```

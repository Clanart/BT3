## Title
Gasless module's `PreAddTx` mutates the shared `knownTxs` map while holding only a read lock, causing concurrent map writes - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The `kaiax/gasless` module's `knownTxs` map (protected by `knownTxsMu sync.RWMutex`) is written to under a write lock (`Lock()`) everywhere except in `PreAddTx`, which acquires only `RLock()` before calling the mutating method `g.knownTxs.add(...)`. Since `sync.RWMutex` allows multiple simultaneous readers, two or more concurrent `PreAddTx` calls (triggered by ordinary concurrent transaction submissions into the tx pool) can execute `g.knownTxs.add()` at the same time, both writing to the same underlying Go map without mutual exclusion. This is exactly the class of bug described in the EVerest advisory: unsynchronized concurrent access to shared state during normal, attacker-reachable operation (here, submitting transactions), which is undefined behavior in Go's map implementation and fatal at runtime ("concurrent map writes").

### Finding Description
`GaslessModule.knownTxs` is a shared, mutable map guarded by `knownTxsMu`: [1](#0-0) 

Every other accessor that mutates the map correctly takes the write lock, e.g. `IsReady`: [2](#0-1) 

and `PreReset`/`PostReset`: [3](#0-2) [4](#0-3) 

However, `PreAddTx` — which is invoked by the tx pool on every incoming transaction, i.e., directly reachable by any unprivileged transaction sender submitting transactions — only acquires a read lock (`RLock`) and then calls the mutating `g.knownTxs.add(tx, TxStatusQueue)`: [5](#0-4) 

Because `sync.RWMutex.RLock()` permits multiple concurrent holders, two transactions submitted concurrently (e.g., via concurrent RPC calls or concurrent tx-pool ingestion goroutines) that both satisfy `g.IsBundleTx(tx)` will both execute `g.knownTxs.add(...)` concurrently while only holding a shared read lock against each other. This is a genuine data race on the underlying map, which in Go triggers a runtime "fatal error: concurrent map writes" crash (not a panic that can be recovered), or in less deterministic cases corrupts map internals leading to undefined behavior — directly analogous to the C++ UB race described in the EVerest CVE, where two code paths mutate shared state concurrently without a proper mutual-exclusion guarantee.

### Impact Explanation
A successful trigger crashes the node process (fatal, non-recoverable runtime error), taking down block production/tx processing on the affected node. If this is more likely to occur on some nodes than others depending on timing/load, it can cause availability divergence across the network (nodes crashing non-deterministically while processing the same transaction set), which is a state-consistency/availability concern beyond simple resource exhaustion — it stems directly from a genuine unsynchronized shared-state race, matching the bug class in the report.

### Likelihood Explanation
Any unprivileged transaction sender can submit multiple qualifying gasless bundle transactions (`IsBundleTx(tx) == true`) concurrently, e.g., via multiple simultaneous RPC submissions or via the tx-pool's normal concurrent tx admission paths. No special privileges are required, and this occurs during normal chain operation whenever the gasless module is enabled and receives concurrent qualifying transactions, matching CVSS AC:H/PR:N/UI:N characteristics.

### Recommendation
Change `PreAddTx` in `kaiax/gasless/impl/tx_pool.go` to acquire `knownTxsMu.Lock()` (write lock) instead of `RLock()`, since the function path can mutate `g.knownTxs` via `add()`. Alternatively, split the read-only checks (`g.knownTxs.get`, `g.knownTxs.numQueue`) from the mutating `add` call and only escalate to a write lock for the mutation, ensuring no concurrent readers can be interleaved with the write.

### Proof of Concept
1. Enable the gasless module on a node.
2. From an unprivileged client, concurrently submit (e.g., via `goroutine`/parallel RPC `eth_sendRawTransaction` calls) two or more valid gasless bundle transactions (`GaslessApproveTx`/`GaslessSwapTx` pairs) such that `IsBundleTx(tx)` is true for each, targeting the tx pool at the same time.
3. Both goroutines invoke `TxPool.AddLocal`/`AddRemote`, which calls the gasless module's `PreAddTx` hook concurrently; each acquires `knownTxsMu.RLock()` and calls `g.knownTxs.add(tx, TxStatusQueue)` concurrently.
4. Under Go's race detector (`-race`) or under sufficient load, this manifests as `fatal error: concurrent map writes`, crashing the node process.

### Citations

**File:** kaiax/gasless/impl/init.go (L60-65)
```go
	currentStateMu sync.Mutex     // even simple GetNonce affects statedb's internal state, hence can't use RWMutex.
	currentState   *state.StateDB // latest state for nonce lookup

	knownTxsMu sync.RWMutex
	knownTxs   *knownTxs
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L38-53)
```go
func (g *GaslessModule) PreAddTx(tx *types.Transaction, local bool) error {
	g.knownTxsMu.RLock()
	defer g.knownTxsMu.RUnlock()

	if knownTx, ok := g.knownTxs.get(tx.Hash()); ok && knownTx.elapsedPromotedOrAddedTime() < KnownTxTimeout {
		return ErrUnableToAddKnownBundleTx
	}

	if g.IsBundleTx(tx) {
		if uint(g.knownTxs.numQueue()) >= g.GetMaxBundleTxsInQueue() {
			return ErrBundleTxQueueFull
		}
		g.knownTxs.add(tx, TxStatusQueue)
	}
	return nil
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L185-187)
```go
func (g *GaslessModule) IsReady(txs map[uint64]*types.Transaction, next uint64, ready types.Transactions) bool {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()
```

**File:** kaiax/gasless/impl/tx_pool.go (L293-295)
```go
func (g *GaslessModule) PreReset(oldHead, newHead *types.Header) []common.Hash {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()
```

**File:** kaiax/gasless/impl/tx_pool.go (L318-320)
```go
func (g *GaslessModule) PostReset(oldHead, newHead *types.Header, queue, pending map[common.Address]types.Transactions) {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()
```

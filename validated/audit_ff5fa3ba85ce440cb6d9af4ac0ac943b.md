### Title
GaslessModule.PreAddTx enforces MaxBundleTxsInQueue under a read-lock, allowing concurrent submitters to overflow the gasless bundle-tx queue - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The external report's root cause is a classic check-then-act race: a count is checked against a fixed maximum, and the check/insert is not atomic with respect to concurrent operations that add multiple entries "at once," letting the collection exceed its declared cap and breaking downstream invariants. The closest analog reachable by an unprivileged transaction sender in this Kaia codebase is `GaslessModule.PreAddTx`, which enforces `MaxBundleTxsInQueue` by checking `knownTxs.numQueue()` and then mutating the shared `knownTxs` map — but the whole operation is guarded only by `g.knownTxsMu.RLock()` (a read lock), not a write lock.

### Finding Description
`PreAddTx` is the hook invoked for every transaction admitted into the pool (`blockchain/tx_pool.go` calls it during `addTx`). It performs a check-then-act sequence on the shared `g.knownTxs` map while holding only a **read** lock: [1](#0-0) 

```go
func (g *GaslessModule) PreAddTx(tx *types.Transaction, local bool) error {
	g.knownTxsMu.RLock()
	defer g.knownTxsMu.RUnlock()
	...
	if g.IsBundleTx(tx) {
		if uint(g.knownTxs.numQueue()) >= g.GetMaxBundleTxsInQueue() {
			return ErrBundleTxQueueFull
		}
		g.knownTxs.add(tx, TxStatusQueue)
	}
	return nil
}
```

`g.knownTxs` is a plain `map[common.Hash]*knownTx` (`kaiax/gasless/impl/tx_counter.go`), and `add()` mutates that map directly: [2](#0-1) 

An `RWMutex.RLock()` allows arbitrarily many goroutines to hold the read lock concurrently. Because Go's `blockchain.TxPool` processes concurrently submitted transactions (e.g., via `AddRemotes`/`AddLocals`/concurrent `eth_sendRawTransaction` RPC calls) on separate goroutines, multiple `PreAddTx` calls for distinct gasless bundle transactions from different senders can execute their `numQueue() >= max` check simultaneously — before any of them has committed its `add()` — and all observe the queue as "not yet full." Each then proceeds to mutate the shared map concurrently, which both (a) races on an unsynchronized map write (undefined behavior / possible panic in Go maps under concurrent write) and (b) allows the enforced `MaxBundleTxsInQueue` bound to be exceeded, exactly mirroring the bug class in the report: a size limit that is supposed to gate insertion into a shared batch/queue is bypassed because the check and the mutation are not atomic with respect to concurrently-processed insertions.

This is analogous to the reported `LibGateway::commitBottomUpMsg` bug, where the `if batch.msgs.length == s.maxMsgsPerBottomUpBatch` check-and-cut was not properly serialized against multiple message commits happening in a single execution context, letting the stored batch exceed its maximum size and later fail downstream validation (`ensureValidCheckpoint`). Here, downstream consumers of `knownTxs` (e.g. `IsReady`, `builder.ExtractTxBundles`) assume the queue/pending bookkeeping respects `MaxBundleTxsInQueue`/`MaxBundleTxsInPending`; if that bound is silently violated, the resource/flow-control guarantee documented in `kaiax/gasless/README.md` and enforced by config (`MaxBundleTxsInQueueFlag`, default 200) is broken.

### Impact Explanation
This bug allows an unprivileged pool of gasless-swap submitters (any public-RPC caller crafting valid `GaslessApproveTx`/`GaslessSwapTx` pairs per KIP-247) to overrun the intended `MaxBundleTxsInQueue` resource limit via concurrent submission, and additionally introduces an unsynchronized concurrent map write on `g.knownTxs` (write under an RLock held by multiple goroutines), which in Go can corrupt the map or cause a runtime panic ("concurrent map writes"), crashing the node process handling transaction admission. A node crash triggered by ordinary transaction submission is a availability-impacting bug reachable from a single class of unprivileged callers (gasless swap users / public-RPC callers), consistent with the allowed analog surfaces (gasless module).

### Likelihood Explanation
Triggering this requires only submitting many valid gasless bundle transactions from many distinct signer addresses concurrently (parallel RPC calls), which any external actor can do without special privileges. The transaction pool's remote-transaction ingestion path is designed to process independent transactions concurrently, so the race window is realistically reachable, not merely theoretical, though the panic/overflow is probabilistic depending on goroutine scheduling and network conditions (hence Medium rather than a deterministic High).

### Recommendation
Change `PreAddTx`'s locking to a write lock (`g.knownTxsMu.Lock()`) for the entire check-then-add sequence so the size check and mutation of `g.knownTxs` are atomic with respect to all other readers/writers, eliminating both the un synchronized map write and the TOCTOU bypass of `MaxBundleTxsInQueue`.

### Proof of Concept
1. Configure a node with a small `--gasless.max-bundle-txs-in-queue` (e.g. 1).
2. From N different signer accounts, craft N valid `GaslessApproveTx`+`GaslessSwapTx`-eligible bundle transactions (each satisfying `IsBundleTx`).
3. Submit all N transactions concurrently to the node's RPC (e.g. N parallel `eth_sendRawTransaction` calls, or directly invoke `pool.AddRemotes` with N transactions so they are dispatched to concurrent processing paths).
4. Because each goroutine's `PreAddTx` call only takes `g.knownTxsMu.RLock()`, multiple goroutines observe `numQueue() < 1` simultaneously and all proceed to `g.knownTxs.add(...)`, both exceeding the configured limit of 1 and racing on the underlying Go map, which under `go test -race` or in production can manifest as `fatal error: concurrent map writes`.

### Citations

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

**File:** kaiax/gasless/impl/tx_counter.go (L45-68)
```go
func (k knownTxs) add(tx *types.Transaction, status int) {
	if tx == nil {
		return
	}

	if ktx, ok := k.get(tx.Hash()); ok {
		ktx.status = status
	} else {
		k[tx.Hash()] = &knownTx{
			tx:           tx,
			addedTime:    time.Time{},
			promotedTime: time.Time{},
			status:       status,
		}
	}

	if status == TxStatusQueue {
		k[tx.Hash()].startAddedTimeIfZero()
	} else if status == TxStatusPending {
		k[tx.Hash()].startPromotedTimeIfZero()
	}

	updateMetrics(&k)
}
```

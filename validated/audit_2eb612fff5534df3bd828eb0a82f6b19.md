Based on my investigation, I found a genuine concurrency bug in the gasless module that is structurally analogous to the reported ALSA bug class (a shared read/write ring/map state that is mutated under an insufficient lock, letting concurrent updaters race with reset/read paths).

### Title
Gasless module `knownTxs` map mutated under `RLock` in `PreAddTx`, causing concurrent map writes / stale bundle-tracking state - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
`GaslessModule.PreAddTx` takes only `knownTxsMu.RLock()` (a shared/read lock) before calling `g.knownTxs.add(tx, TxStatusQueue)`, which performs a plain, unsynchronized Go map write (`k[tx.Hash()] = &knownTx{...}`) on the shared `knownTxs` map.

### Finding Description
`PreAddTx` is the hook the tx pool invokes for every incoming transaction (approve/swap bundle candidates) to register them in `g.knownTxs`: [1](#0-0) 

`knownTxs.add` is not itself synchronized — it assumes the caller holds an *exclusive* lock — yet it performs a write to the underlying map (`k[tx.Hash()] = ...`) and calls `updateMetrics`, which iterates the same map: [2](#0-1) 

Because `PreAddTx` only acquires `knownTxsMu.RLock()` rather than `Lock()`, two concurrent invocations of `PreAddTx` for two different incoming gasless-bundle candidate transactions (e.g. an approve tx and a swap tx submitted by different senders at the same time) are permitted to run in parallel while both write to the same underlying Go map. This mirrors the ALSA bug class exactly: a shared piece of ring/queue state (`qlen/head/tail` in the kernel case, the `knownTxs` map contents here) is normally serialized by one lock (`q->lock` / `knownTxsMu`), but one code path (`readq_clear` / `PreAddTx`) takes an insufficient form of the lock (no lock / read-lock) while other paths take the full/write lock, letting concurrent writers race.

The other consumers of the same map correctly take the exclusive `Lock()`: [3](#0-2) [4](#0-3) 

Since `PreAddTx` is a `kaiax.TxPoolModule` hook invoked from the tx pool's `addTx`/`AddRemote`/`AddLocal` insertion paths for every submitted transaction — reachable directly by any unprivileged transaction sender submitting approve/swap gasless-bundle transactions — concurrent submissions can trigger a genuine Go data race: two goroutines writing to the same `map[common.Hash]*knownTx` without an exclusive lock. In Go, concurrent unsynchronized map writes are detected at runtime and cause `fatal error: concurrent map writes`, crashing the process (not a recoverable panic), or, if timing avoids the runtime's crash detector, can corrupt map internals leading to further undefined behavior (bad iteration, lost/duplicate entries) which corrupts bundle-timeout bookkeeping (`TxStatusQueue`/`TxStatusPending` tracking used to decide `MaxBundleTxsInQueue` / `MaxBundleTxsInPending` limits and to filter transactions in `PostReset`).

### Impact Explanation
A node process crash (denial of service) triggerable by any unprivileged party submitting ordinary gasless approve/swap transactions concurrently via public RPC or gossip is High impact: it takes down block production/serving for the affected node. Even absent an immediate crash, corrupted `knownTxs` state can cause the gasless queue/pending caps and timeout accounting to diverge from actual pool state, potentially allowing more bundle transactions into the pool than the configured `MaxBundleTxsInQueue`/`MaxBundleTxsInPending` limits intend, or causing legitimate bundle txs to be incorrectly dropped/rejected.

### Likelihood Explanation
Any two transactions handled concurrently by the tx pool's insertion path (which is the normal mode of operation — multiple RPC callers, or RPC + p2p broadcast, submitting txs simultaneously) that both go through `PreAddTx` (i.e., where at least one is a gasless approve/swap tx, since the write path is inside the `IsBundleTx` branch, but the read side of `get`/status checks always runs) will race on the shared map lock discipline. Because Go's race detector / runtime panics deterministically on concurrent map writes under typical load, likelihood of triggering a crash under sustained concurrent traffic is high, though exact timing is nondeterministic without a race detector or heavy concurrent load.

### Recommendation
Change `PreAddTx` to acquire `knownTxsMu.Lock()` (exclusive) instead of `RLock()` before calling `g.knownTxs.add(...)`, matching the locking discipline used by `PreReset`/`PostReset`/`PostInsertBlock`-equivalent write paths. If read-only fast-path checks are desired for performance, split the function so the exclusive lock is only held for the mutation section, but never allow `add`/`delete`/mutating calls to occur while holding only `RLock()`.

### Proof of Concept
1. Configure a node with the gasless module enabled and `MaxBundleTxsInQueue` set to a reasonable value.
2. From two or more concurrent RPC clients (or via concurrent goroutines calling `AddLocal`/`AddRemotes`), submit distinct valid "approve" and "swap" gasless-bundle transactions simultaneously and repeatedly (loop with high concurrency), each hitting `TxPool.addTx` → `GaslessModule.PreAddTx`.
3. Because `PreAddTx` only takes `knownTxsMu.RLock()` while writing to `g.knownTxs` (a plain Go map) via `add()`, running this concurrently under Go's race detector (`go test -race`) or under sustained load will surface `fatal error: concurrent map writes`, crashing the node process — reachable purely by unprivileged transaction submission, with no special privileges required.

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

**File:** kaiax/gasless/impl/tx_pool.go (L292-316)
```go
// PreReset removes timed out tx from the tx pool and knownTxs.
func (g *GaslessModule) PreReset(oldHead, newHead *types.Header) []common.Hash {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

	drops := make([]common.Hash, 0)

	for hash, knownTx := range *g.knownTxs {
		// remove pending timed out tx from tx pool
		if knownTx.status == TxStatusPending && knownTx.elapsedPromotedTime() >= PendingTimeout {
			drops = append(drops, hash)
		}
		// remove queue timed out tx from tx pool
		if knownTx.status == TxStatusQueue && knownTx.elapsedAddedTime() >= QueueTimeout {
			drops = append(drops, hash)
		}
		// remove known timed out tx from knownTxs
		if knownTx.elapsedPromotedOrAddedTime() >= KnownTxTimeout {
			g.knownTxs.delete(hash)
		}
	}

	return drops
}

```

**File:** kaiax/gasless/impl/tx_pool.go (L317-344)
```go
// PostReset re-categorizes knownTxs based on the current txpool.queue and txpool.pending.
func (g *GaslessModule) PostReset(oldHead, newHead *types.Header, queue, pending map[common.Address]types.Transactions) {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

	flattenedQueue := make(map[common.Hash]*types.Transaction)
	flattenedPending := make(map[common.Hash]*types.Transaction)
	for _, txs := range queue {
		for _, tx := range txs {
			flattenedQueue[tx.Hash()] = tx
		}
	}
	for _, txs := range pending {
		for _, tx := range txs {
			flattenedPending[tx.Hash()] = tx
		}
	}

	for _, knownTx := range *g.knownTxs {
		if _, ok := flattenedQueue[knownTx.tx.Hash()]; ok {
			g.knownTxs.add(knownTx.tx, TxStatusQueue)
		} else if _, ok := flattenedPending[knownTx.tx.Hash()]; ok {
			g.knownTxs.add(knownTx.tx, TxStatusPending)
		} else {
			g.knownTxs.add(knownTx.tx, TxStatusDemoted)
		}
	}
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

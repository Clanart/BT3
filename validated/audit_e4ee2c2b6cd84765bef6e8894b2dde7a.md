Confirmed root cause: `GaslessModule.PreAddTx` acquires only a **read lock** (`knownTxsMu.RLock()`) but then performs an unsynchronized **write** into the shared `knownTxs` map via `g.knownTxs.add(tx, TxStatusQueue)`. [1](#0-0) 

### Title
Concurrent map write race in `GaslessModule.PreAddTx` due to write under `RLock` - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
`PreAddTx` is the `TxPoolModule` hook invoked on every transaction submitted to the tx pool (`AddLocal`/`AddRemote`, i.e., reachable by any transaction sender via RPC or p2p). It takes `g.knownTxsMu.RLock()` — a *read* lock — and, while holding only that read lock, calls `g.knownTxs.add(tx, TxStatusQueue)`, which mutates the underlying `map[common.Hash]*knownTx` (`knownTxs.add`) by inserting/updating entries. `sync.RWMutex.RLock()` permits multiple concurrent readers to proceed simultaneously, so two or more goroutines calling `PreAddTx` at the same time (e.g., concurrent gasless approve/swap tx submissions from different senders or peers) can concurrently write to the same Go map with no exclusion between them. [2](#0-1) [3](#0-2) 

This mirrors the CVE-2026-23231 bug class: an object is exposed to a shared, concurrently-accessed structure without proper synchronization discipline between the "read" path and the mutating path, and other legitimate holders of that structure (readers/writers elsewhere: `IsReady`, `PreReset`, `PostReset` which correctly use `Lock()`) can race with it.

### Finding Description
`TxPoolModule.PreAddTx` is called for every incoming transaction in the tx pool's add path. Any external actor able to submit transactions concurrently (multiple gasless-approve/gasless-swap senders hitting `eth_sendRawTransaction`/p2p tx propagation at the same time) can trigger concurrent invocations of `PreAddTx`. Because `RLock()` only prevents concurrency with a `Lock()` holder — not with other `RLock()` holders — two goroutines can simultaneously execute `g.knownTxs.add(...)`, which performs an unguarded Go map write (`k[tx.Hash()] = &knownTx{...}`) inside `knownTxs.add`. Concurrent writes to the same Go map from multiple goroutines is undefined behavior in Go and is explicitly detected/fataled by the runtime ("fatal error: concurrent map writes"), immediately crashing the process. In non-crashing races, it can also corrupt the map's internal bucket structure or lose an update to `addedTime`, silently corrupting the accounting used for `numQueue()`/`numExecutable()` limits and later reads in `IsReady`, `PreReset`, and `PostReset` (which do take proper `Lock()`s, but read a `knownTxs` map whose invariants were already violated by the race).

### Impact Explanation
A successful trigger crashes the node process with a Go runtime fatal error (unrecoverable, not a panic that can be caught), producing a denial-of-service against any Kaia node running the gasless module (both endpoint nodes and consensus nodes lending gas, per `NodeType` checks in `Init`). Because the trigger is simply submitting ordinary gasless-eligible transactions concurrently — something any unprivileged transaction sender can do via public RPC — this is remotely reachable without needing malicious peers, validators, or special privileges. If nodes crash non-deterministically (depending on scheduling), this can also cause state/behavior divergence between honest nodes (some crash, some don't, some see corrupted `knownTxs` bookkeeping affecting which bundle txs get promoted), which can indirectly affect gasless-tx bundle admission and block-building consistency across the network.

### Likelihood Explanation
High reachability: the gasless module is enabled by default when not explicitly disabled (`mGaslessEnabled` check in `node/cn/backend.go`), and `PreAddTx` runs on the hot path of every transaction insertion. Triggering the race only requires submitting multiple gasless approve/swap transactions concurrently (from different accounts or via multiple RPC connections) — no special timing beyond ordinary concurrent load, no cryptographic material, no validator/peer privileges. Go's race detector / runtime concurrent-map-write panic makes the crash deterministic once two writers overlap, and public RPC endpoints naturally see concurrent submissions under normal network load.

### Recommendation
Change `PreAddTx` to take the exclusive `g.knownTxsMu.Lock()` (matching the pattern already used correctly in `IsReady`, `PreReset`, and `PostReset`) whenever it may call `g.knownTxs.add`, or restructure so the read (`get`) and conditional write (`add`) happen atomically under a single write lock for the whole function body. [4](#0-3) [5](#0-4) 

### Proof of Concept
1. Enable the gasless module on a Kaia node (default configuration, `mGasless.IsDisabled() == false`).
2. From two or more independent clients/goroutines, concurrently submit distinct valid `GaslessApproveTx`/`GaslessSwapTx` transactions (satisfying `IsBundleTx`) to the node's public RPC (`eth_sendRawTransaction`) such that both pass validation and reach `pool.addTx()` → `PreAddTx` at overlapping times.
3. Both goroutines execute `PreAddTx`, both acquire `g.knownTxsMu.RLock()` concurrently (allowed, since `RLock` is shared), and both proceed to call `g.knownTxs.add(tx, TxStatusQueue)`, performing concurrent writes to the same `map[common.Hash]*knownTx`.
4. Under Go's runtime map-write race detection, this results in `fatal error: concurrent map writes`, crashing the node process; under `-race` this is flagged explicitly as a data race.

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

**File:** kaiax/gasless/impl/tx_pool.go (L186-187)
```go
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()
```

**File:** kaiax/gasless/impl/tx_pool.go (L294-295)
```go
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()
```

**File:** kaiax/gasless/impl/tx_counter.go (L43-68)
```go
type knownTxs map[common.Hash]*knownTx

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

**File:** kaiax/gasless/impl/init.go (L63-65)
```go
	knownTxsMu sync.RWMutex
	knownTxs   *knownTxs
}
```

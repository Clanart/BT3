### Title
Concurrent map mutation in `PreAddTx` under an `RLock` allows racing gasless-transaction submissions to corrupt/crash the `knownTxs` tracking map - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
`GaslessModule.PreAddTx`, which is invoked once per incoming transaction from `TxPool.add()` for every gasless (approve/swap) tx an unprivileged sender submits, takes only a **read** lock (`g.knownTxsMu.RLock()`) before calling `g.knownTxs.add(tx, TxStatusQueue)`, which **writes** into the underlying `map[common.Hash]*knownTx`. This mirrors the CVE-2017-15299 bug class: an "already exists but is being concurrently mutated/instantiated" entry is written to a shared structure without the exclusive lock that its own invariants require, enabling a race that corrupts state or crashes the process (Go's runtime fatally aborts on concurrent map write, which cannot be recovered — a hard node crash, i.e. denial of service).

### Finding Description
`PreAddTx` is defined as: [1](#0-0) 

It takes `g.knownTxsMu.RLock()` (a shared/read lock) and then, if the tx is a bundle/gasless tx, calls `g.knownTxs.add(tx, TxStatusQueue)`. `knownTxs.add` mutates the map in-place: [2](#0-1) 

Every other accessor of `g.knownTxs` in `tx_pool.go` correctly takes the **write** lock (`g.knownTxsMu.Lock()`), e.g. `PreReset`: [3](#0-2) 

But `PreAddTx` is the entry point reached directly from a remote/public caller submitting a transaction to the node's tx pool: `TxPool.add()` calls `module.PreAddTx(tx, local)` for every incoming tx before any other pool-level locking of the gasless module's own state: [4](#0-3) 

Because `RLock()` allows multiple goroutines to enter concurrently, two transactions arriving through independent code paths that both dispatch into `PreAddTx` at the same moment (or one thread in `PreAddTx` racing with another thread that also legitimately holds `RLock` for a read, such as `GetLendTxGenerator`/`isReady`/`isApproveTxReady` style getters that also read `g.knownTxs` under `RLock`) can execute `k[tx.Hash()] = &knownTx{...}` and `updateMetrics(&k)` concurrently with another reader or writer iterating the same map, which is a well-known Go data race. Concurrent read+write or write+write access to a plain Go map is undefined behavior and typically results in "fatal error: concurrent map read and map write", an unrecoverable process crash — analogous to the NULL-pointer/crash outcome in CVE-2017-15299 where the kernel mishandled a key that already existed but was being concurrently instantiated.

### Impact Explanation
Any unprivileged transaction sender who can submit standard `GaslessApproveTx`/`GaslessSwapTx` transactions (or two such senders submitting concurrently) can trigger simultaneous `PreAddTx` invocations. Because `TxPool.add()` (and thus `PreAddTx`) is entered before the transaction pool's own outer mutex fully serializes gasless-module state in all cases, and because the module explicitly acquires only `RLock` while performing a write, remote/public callers can force the node process to crash with a fatal runtime error (Go maps panic non-recoverably on detected concurrent access), producing a denial of service on validator/full nodes running the gasless module. This matches the "Medium" severity ceiling of the CVE analog (local/low-privilege trigger, availability impact only, no direct fund theft), and is directly reachable by an ordinary gasless-transaction sender.

### Likelihood Explanation
The trigger requires no special privilege — an attacker only needs to submit ordinary gasless transactions (approve/swap) concurrently (from one or more accounts, potentially via multiple RPC connections or peer gossip) to increase the odds of the race firing. Go's race detector would immediately flag this as `RLock` + map write; in a live network with default GOMAXPROCS>1 and moderate transaction throughput, the concurrent-map-write panic is a realistic, though probabilistic, trigger — likelihood is elevated by the fact that `PreAddTx` runs on essentially every incoming gasless transaction and any concurrent gasless reader path (e.g., anything else holding `g.knownTxsMu.RLock()` while iterating/reading `g.knownTxs`) can collide with it.

### Recommendation
Change `PreAddTx` to acquire the full write lock (`g.knownTxsMu.Lock()`) instead of `RLock()` whenever it may call `g.knownTxs.add(...)`, matching the locking discipline used in `PreReset`/`PostReset`. Audit all other call sites in `kaiax/gasless/impl/tx_pool.go` and `tx_counter.go` that read `g.knownTxs` under `RLock` to ensure no writer path is reachable concurrently with those readers, and add a `-race`-enabled concurrency test analogous to the existing `tests/resend_nil_test.go` benchmark (`BenchmarkResendNilDereference`), which already demonstrates the project's established pattern for catching this exact class of bug in the tx pool.

### Proof of Concept
1. Start a node with the gasless module enabled.
2. From two or more goroutines/RPC clients, concurrently submit valid `GaslessApproveTx`/`GaslessSwapTx` transactions (different senders/nonces so they are not rejected by the pool for unrelated reasons) so that each submission passes into `TxPool.add()` → `GaslessModule.PreAddTx()` → `g.knownTxs.add(...)` at roughly the same time.
3. Simultaneously (or in the same run), exercise any other exported gasless-module API/path that reads `g.knownTxs` under `g.knownTxsMu.RLock()` (e.g. reset/promotion paths), similar to the pattern in `tests/resend_nil_test.go`'s `BenchmarkResendNilDereference`, which pairs `TxPool.AddRemotes()` with `TxPool.CachedPendingTxsByCount()` under `-race` to reproduce a nil-pointer/concurrent-access crash.
4. Run with `go test -race`; the concurrent map write in `knownTxs.add` under only a read lock should surface as a `fatal error: concurrent map read and map write` (a hard, unrecoverable crash) rather than a `-race` warning alone, once real map resizing/rehashing collides with a concurrent reader/writer.

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

**File:** kaiax/gasless/impl/tx_pool.go (L292-311)
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

**File:** blockchain/tx_pool.go (L1139-1148)
```go
func (pool *TxPool) add(tx *types.Transaction, local bool) (bool, error) {
	for _, module := range pool.modules {
		if module.IsModuleTx(tx) {
			err := module.PreAddTx(tx, local)
			if err != nil {
				return false, err
			}
			break
		}
	}
```

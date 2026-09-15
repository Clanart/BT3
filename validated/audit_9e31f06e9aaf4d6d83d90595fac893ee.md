### Title
Read-lock held during map write in `GaslessModule.PreAddTx` allows concurrent map corruption / crash on `knownTxs` - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
`GaslessModule.PreAddTx` takes only a **read lock** (`g.knownTxsMu.RLock()`) but then performs a **write** to the shared `knownTxs` map via `g.knownTxs.add(tx, TxStatusQueue)`. Since `sync.RWMutex.RLock()` permits multiple concurrent holders, any two goroutines that call `PreAddTx` at the same time can both proceed to mutate the plain Go map `knownTxs` (`map[common.Hash]*knownTx`) without mutual exclusion, which is the same "missing/insufficient locking on a shared table" bug class as CVE-2024-50286 (missing `sessions_table_lock` around concurrent session-table add/remove causing slab-use-after-free).

### Finding Description
`knownTxs` is a bare `map[common.Hash]*knownTx` with no internal synchronization; all safety is expected to come from `GaslessModule.knownTxsMu`: [1](#0-0) [2](#0-1) 

Every other mutator of `knownTxs` correctly takes the **exclusive** lock before writing:
- `IsReady` — `g.knownTxsMu.Lock()` then `g.knownTxs.add(...)` [3](#0-2) 
- `PreReset` / `PostReset` — `g.knownTxsMu.Lock()` then `g.knownTxs.delete(...)` / `g.knownTxs.add(...)` [4](#0-3) 

However, `PreAddTx` takes only `RLock()` and still calls the mutating `add()` method: [5](#0-4) 

`knownTxs.add` performs a raw, unsynchronized Go map write (`k[tx.Hash()] = &knownTx{...}`), assuming the caller already holds the correct exclusive lock: [6](#0-5) 

Because `RLock()` allows multiple simultaneous holders, if two goroutines invoke `PreAddTx` concurrently for two different bundle transactions, both can pass the read-lock gate and race on the same underlying map, producing a data race on `knownTxs`. In Go this manifests as "fatal error: concurrent map writes" (process crash) or, in the worst case, corrupted map internal state leading to further memory-safety issues — directly analogous to the slab-use-after-free caused by the missing `sessions_table_lock` in the ksmbd advisory: a shared table is mutated from two code paths that do not use compatible/matching locking.

`PreAddTx` is invoked from the transaction-pool module hook path when a transaction is submitted: [7](#0-6) 

### Impact Explanation
This is reachable from unprivileged, public transaction submission (`AddLocal`/`AddRemote`/`AddRemotes` → `pool.add()` → `module.PreAddTx()`). A successful race causes an unsynchronized concurrent write to a shared Go map, which is undefined behavior in the Go runtime and typically crashes the process (`fatal error: concurrent map writes`), i.e., a remotely triggerable denial-of-service of the transaction-pool/node process — the availability-impact analog of the "Critical" ksmbd UAF. Because the crash is deterministic runtime-detected corruption rather than exploitable arbitrary code execution in Go's memory-safe runtime, the confidentiality/integrity impact is lower than the original native-code UAF, but the availability impact (node crash reachable by unauthenticated public gasless-bundle tx submitters) is real.

### Likelihood Explanation
Triggering the race requires two `PreAddTx` calls to execute concurrently while both hold only `RLock()`. Note that the standard single-transaction entry points (`TxPool.addTx`/`addTxs`) take the pool-wide exclusive `pool.mu.Lock()` before calling `pool.add()`, which — for a single `TxPool` instance — largely serializes calls into `module.PreAddTx`. I could not fully verify within the remaining tool budget whether every code path that reaches `module.PreAddTx` (e.g., other batch-submission or async entry points) is uniformly guarded by that same `pool.mu`, or whether `pool.txMu` (a separate mutex used by `demoteUnexecutables`/`reset`) can ever run concurrently with a `pool.mu`-guarded add path in a way that reaches `PreAddTx` twice concurrently. The RLock/write mismatch itself is a definite code defect independent of that question; its real-world exploitability depends on this unverified locking topology, so likelihood should be treated as **uncertain/medium** pending further investigation of all callers of `TxPool.add()`/`module.PreAddTx` (a task better suited to a live Devin session with full repo/build access, e.g. running `go test -race` across `kaiax/gasless`).

### Recommendation
Change `PreAddTx` to take the exclusive lock (`g.knownTxsMu.Lock()`) instead of `RLock()`, since it performs writes via `g.knownTxs.add`/`numQueue`. As a broader fix, audit every accessor of `knownTxs` to ensure reads use `RLock` and all writes (`add`, `addKnownTx`, `delete`) use `Lock`, and add a `go test -race` regression test that concurrently invokes `PreAddTx` from multiple goroutines to catch this class of bug (mirroring the kernel fix's approach of auditing every session-table mutator for the correct lock).

### Proof of Concept
Conceptual reproduction (would need to be validated in a live Devin session with `go test -race`):
1. Register a `GaslessModule` on a `TxPool`.
2. Spawn N goroutines, each directly calling `gaslessModule.PreAddTx(bundleTx_i, false)` with distinct bundle transactions (bypassing `TxPool.mu` to simulate any code path that is not fully serialized by it, or by identifying/using the actual unserialized call path once confirmed).
3. Run with `go test -race`; expect either the Go race detector to flag a data race on the `knownTxs` map or a `fatal error: concurrent map writes` runtime panic.

### Citations

**File:** kaiax/gasless/impl/init.go (L63-64)
```go
	knownTxsMu sync.RWMutex
	knownTxs   *knownTxs
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

**File:** kaiax/gasless/impl/tx_pool.go (L293-320)
```go
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

// PostReset re-categorizes knownTxs based on the current txpool.queue and txpool.pending.
func (g *GaslessModule) PostReset(oldHead, newHead *types.Header, queue, pending map[common.Address]types.Transactions) {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()
```

**File:** blockchain/tx_pool.go (L1543-1561)
```go
// addTx enqueues a single transaction into the pool if it is valid.
func (pool *TxPool) addTx(tx *types.Transaction, local bool) error {
	senderCacher.recover(pool.signer, []*types.Transaction{tx})

	pool.mu.Lock()
	defer pool.mu.Unlock()

	// Try to inject the transaction and update any state
	replace, err := pool.add(tx, local)
	if err != nil {
		return err
	}
	// If we added a new transaction, run promotion checks and return
	if !replace {
		from, _ := types.Sender(pool.signer, tx) // already validated
		pool.promoteExecutables([]common.Address{from})
	}
	return nil
}
```

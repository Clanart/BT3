### Title
Race condition on `GaslessModule.knownTxs` map due to read-lock guarding a write in `PreAddTx()` - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
`GaslessModule.PreAddTx()`, the hook invoked whenever a transaction is submitted to the tx pool (`AddLocal`/`AddRemote`/`AddRemotes`, reachable by any unprivileged transaction sender or public-RPC caller), acquires only a **read lock** (`g.knownTxsMu.RLock()`) but then performs a **write** into the shared `knownTxs` map via `g.knownTxs.add(tx, TxStatusQueue)`. Because `sync.RWMutex.RLock()` permits multiple concurrent holders, two or more goroutines invoking `PreAddTx()` concurrently (e.g. via concurrent `eth_sendRawTransaction` calls or batched `AddRemotes`) can concurrently mutate the plain Go map `knownTxs` (`map[common.Hash]*knownTx`) with no internal synchronization of its own. This is the same class of bug as CVE-2022-48830 (`isotp_rcv`): concurrent reception paths mutate shared state that the code assumes is single-threaded/serialized, but the actual locking discipline doesn't guarantee that.

### Finding Description
`GaslessModule` declares `knownTxsMu sync.RWMutex` to protect the `knownTxs` map (`kaiax/gasless/impl/init.go`): [1](#0-0) 

In `PreAddTx()`, the code takes only a read lock even though it can call `g.knownTxs.add(...)`, a map write: [2](#0-1) 

`knownTxs.add()` is a bare, unsynchronized map write (`k[tx.Hash()] = &knownTx{...}`), with no lock of its own — it fully relies on the caller's `knownTxsMu`: [3](#0-2) 

Because `RLock()` is non-exclusive, if two goroutines call `PreAddTx()` at the same time for two different bundle-eligible (approve/swap) transactions, both can enter the critical section simultaneously and race on `k[tx.Hash()] = ...` — an unsynchronized concurrent Go map write. This is undefined behavior in Go and will either:
- trigger `fatal error: concurrent map writes` (an unrecoverable process crash), or
- corrupt the map's internal structure, causing a later panic in any of the other methods that iterate the same map under a (correctly acquired) lock, such as `numQueue()`, `numPending()`, `numExecutable()`, or `PostReset()`/`PreReset()` (`kaiax/gasless/impl/tx_pool.go` lines 293-343, `kaiax/gasless/impl/tx_counter.go` lines 94-122).

Other call sites in the same module correctly use `Lock()` (exclusive) before mutating `knownTxs` — e.g. `IsReady()`, `PreReset()`, `PostReset()` all call `g.knownTxsMu.Lock()`. Only `PreAddTx()` uses `RLock()` while still writing, which is the root-cause defect analogous to the missing/insufficient locking in `isotp_rcv()`.

### Impact Explanation
`PreAddTx` is a `kaiax.TxPoolModule` hook that fires on every transaction submitted through the standard transaction-pool ingestion path (`AddLocal`, `AddRemote`, `AddRemotes`), which is reachable by any public-RPC caller submitting raw transactions, with no special privilege required. Triggering the race requires nothing more than submitting multiple gasless approve/swap transactions concurrently (e.g., via parallel RPC connections), which any external, unauthenticated user can do. The result is a crash (`fatal error: concurrent map writes`) or corrupted internal accounting for the gasless module, which can:
- Crash the node process (denial of service against CNs/ENs running the gasless module), or
- Corrupt `knownTxs` bookkeeping used for bundle promotion/queue-limit enforcement, potentially causing incorrect promotion decisions (state divergence in tx-pool behavior between nodes) or panics in the block-building path (`ExtractTxBundles`/`worker.go` consumers of the module).

This is High severity: it is remotely triggerable without authentication by a single (or a couple of concurrent) unprivileged transaction submission(s), and results in node crash / denial of service, matching the CVE analog's real-world impact class.

### Likelihood Explanation
Likelihood is high given:
- No privileged access needed — reachable by any transaction sender or RPC client.
- Requires only ordinary concurrent submission of gasless-eligible transactions (approve/swap), which is normal usage pattern under load (multiple RPC clients submitting transactions simultaneously, or batch `AddRemotes` calls processed with worker parallelism).
- Go's race detector / production Go runtime will actively detect concurrent map writes and immediately crash the process (`fatal error: concurrent map writes` is not a panic that can be recovered), making exploitation reliable and repeatable under concurrent load, similar to the syzkaller-discovered concurrent-CAN-frame-reception race in the original CVE.

### Recommendation
Change `PreAddTx()` to acquire an exclusive lock (`g.knownTxsMu.Lock()` / `defer g.knownTxsMu.Unlock()`) instead of `RLock()`, since it performs a write via `g.knownTxs.add(...)`. Additionally, audit all other read/write usages of `knownTxsMu` and `knownTxs` to ensure every method that mutates the map (directly or indirectly) uses the exclusive `Lock()`, and only pure read-only accessors use `RLock()`.

### Proof of Concept
1. Start a node with the gasless module enabled (`GaslessConfig.Disable = false`).
2. From an external client, submit (via `eth_sendRawTransaction` / `AddRemotes`) two or more valid gasless approve/swap transactions from different sender accounts concurrently, in tight parallel loops, so that `TxPool.addTx()` → `module.PreAddTx()` is invoked concurrently for multiple goroutines (this is the standard concurrent RPC ingestion path; no special pool internals need to be bypassed).
3. Because `PreAddTx()` only takes `g.knownTxsMu.RLock()` before calling `g.knownTxs.add()` (a raw map write), concurrent goroutines will race on the `knownTxs` map.
4. Running the node with Go's race detector (`-race`) will immediately flag the data race; under normal (non-race) production builds under sufficient concurrency, this manifests as `fatal error: concurrent map writes`, crashing the node process.

### Citations

**File:** kaiax/gasless/impl/init.go (L63-64)
```go
	knownTxsMu sync.RWMutex
	knownTxs   *knownTxs
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

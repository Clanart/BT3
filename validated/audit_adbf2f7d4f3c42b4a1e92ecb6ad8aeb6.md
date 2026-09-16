### Title
Race condition causes concurrent/unsynchronized map write to `knownTxs` in kaiax gasless module - (File: `kaiax/gasless/impl/tx_pool.go`)

### Summary
`GaslessModule.PreAddTx`, the tx-pool hook invoked whenever a transaction is submitted to the pool, acquires only a **read** lock (`g.knownTxsMu.RLock()`) but then performs a **write** to the shared `knownTxs` map (`g.knownTxs.add(tx, TxStatusQueue)`) inside that read-locked section. Since `sync.RWMutex.RLock()` allows multiple concurrent readers, two or more goroutines calling `PreAddTx` concurrently (e.g., for two different gasless transactions submitted at the same time) can both pass the `RLock()` and then both mutate the underlying Go map concurrently without any exclusion, which is the same bug class as the reported EVerest issue: an unsynchronized read/write pattern around a shared map that is supposed to be protected by a single mutex but is instead accessed under a lock mode that does not actually serialize the mutating operation.

### Finding Description
`PreAddTx` is defined as: [1](#0-0) 

It takes `g.knownTxsMu.RLock()` (a read lock), then, when the submitted transaction is a gasless bundle transaction, calls `g.knownTxs.add(tx, TxStatusQueue)`, which mutates the `knownTxs` map (assignment `k[tx.Hash()] = &knownTx{...}`, and status/time-field mutation on existing entries): [2](#0-1) 

Because `RLock()`/`RUnlock()` only prevents concurrent *writers* from starting while a reader holds the lock — it does **not** prevent two readers from running the "write" path (`add`) at the same time — two transactions arriving concurrently (which is the normal operating mode of a transaction pool receiving submissions from multiple RPC callers) can trigger two goroutines executing `k[tx.Hash()] = &knownTx{...}` on the same underlying Go map concurrently. In Go, concurrent unsynchronized writes to the same map are undefined behavior: the runtime can detect it and crash the node with `fatal error: concurrent map writes`, or — if not caught by the runtime's race detector — silently corrupt the map's internal structure, producing entries that are lost, duplicated status transitions, or torn `knownTx` values (a struct field, e.g. `status`, being read while updated), analogous to the EVerest `std::map<std::optional>` container/optional corruption described in CVE-2026-26072.

The `knownTxs` map tracks which gasless bundle transactions are currently queued/pending, and is read/written not only in `PreAddTx` (via `IsReady`, `PreReset`, `PostReset` — all correctly under `Lock()`) but also incorrectly written under `RLock()` here in `PreAddTx`, which is the sole call site with this mismatch. [3](#0-2) [4](#0-3) [5](#0-4) 

### Impact Explanation
An unprivileged transaction sender can reach this path simply by submitting a gasless approve/swap transaction via the public RPC (`eth_sendRawTransaction`); `IsBundleTx`/`IsModuleTx` gate is satisfied by any correctly-formed KIP-247 gasless transaction. If two or more gasless transactions are submitted concurrently (trivially achievable by any set of unrelated senders, or a single attacker firing multiple submissions in parallel), the underlying `map[common.Hash]*knownTx` in `knownTxs` can be corrupted or the node can crash with a Go runtime fatal error on concurrent map write, which is a denial-of-service on the affected node process. Beyond a crash, a corrupted/inconsistent `knownTxs` map can cause a node's local view of "how many bundle transactions are pending/queued" (used by `numQueue()`, `numExecutable()`, `IsReady()`) to diverge from other honest nodes' views, changing which gasless transactions each node promotes/bundles into blocks — a state-divergence risk in transaction promotion/bundling decisions across nodes.

### Likelihood Explanation
Any unprivileged party can submit multiple gasless transactions to a node's RPC endpoint at the same time (no special timing precision or privileged access required), so the race is straightforward to trigger under ordinary network conditions with concurrent RPC submissions — matching the CVSS vector characteristics of the referenced CVE (`AC:H` reflects that a precise interleaving is needed, but no privileges or user interaction beyond submitting transactions are required).

### Recommendation
Change `PreAddTx` to acquire the full write lock (`g.knownTxsMu.Lock()`/`Unlock()`) instead of `RLock()`/`RUnlock()`, since it performs a map mutation via `g.knownTxs.add(...)`, consistent with the locking pattern already used correctly in `IsReady`, `PreReset`, and `PostReset`.

### Proof of Concept
1. Start a node with the gasless module enabled.
2. From two separate goroutines/RPC clients, concurrently call `eth_sendRawTransaction` with two distinct, valid gasless bundle transactions (e.g., two different senders' `GaslessApproveTx`s) at the same instant, repeating in a tight loop to increase the chance of overlapping execution inside `PreAddTx`.
3. Because both calls reach `g.knownTxsMu.RLock()` concurrently and then both execute `g.knownTxs.add(tx, TxStatusQueue)` (a map write) without any write-exclusive lock, the Go runtime map implementation is invoked concurrently from two goroutines for the same map — this either panics the process with `fatal error: concurrent map writes` or leaves `g.knownTxs` in an inconsistent state (e.g., missing entry, wrong `status`), analogous to the `evse_soc_map` corruption described in CVE-2026-26072/GHSA-9xwc-49c4-p79v.

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

**File:** kaiax/gasless/impl/tx_pool.go (L185-187)
```go
func (g *GaslessModule) IsReady(txs map[uint64]*types.Transaction, next uint64, ready types.Transactions) bool {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()
```

**File:** kaiax/gasless/impl/tx_pool.go (L293-296)
```go
func (g *GaslessModule) PreReset(oldHead, newHead *types.Header) []common.Hash {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

```

**File:** kaiax/gasless/impl/tx_pool.go (L318-321)
```go
func (g *GaslessModule) PostReset(oldHead, newHead *types.Header, queue, pending map[common.Address]types.Transactions) {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

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

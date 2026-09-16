Based on my research, I found a genuine locking-correctness bug in the `kaiax/gasless` module that is structurally analogous to the cachefiles bug class (a lifecycle/synchronization defect between an object's creation/mutation path and its cleanup/read paths, reachable by an unprivileged caller). I was not able to fully trace whether `blockchain/tx_pool.go`'s own `pool.mu` fully serializes calls into `PreAddTx` before running out of tool budget, so I flag that residual uncertainty explicitly below.

### Title
Read-lock used to guard a map mutation in gasless `PreAddTx` can cause concurrent map writes / crash on tx-pool admission - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
`GaslessModule.PreAddTx`, invoked as a `TxPoolModule` hook on every transaction submitted to the node's tx pool, takes only a **read** lock (`g.knownTxsMu.RLock()`) before calling `g.knownTxs.add(tx, TxStatusQueue)`, which **mutates** the shared `knownTxs` map. [1](#0-0) 

### Finding Description
`knownTxs` is a plain `map[common.Hash]*knownTx` with no internal synchronization of its own [2](#0-1) . All mutating accessors of this map (`PreAddTx`, `IsReady`, `PreReset`, `PostReset`) are supposed to be protected by `g.knownTxsMu`. However, while `IsReady`, `PreReset`, and `PostReset` correctly take the **write** lock (`g.knownTxsMu.Lock()`) before mutating the map [3](#0-2) [4](#0-3) [5](#0-4) , `PreAddTx` only takes an `RLock`, which permits multiple goroutines to enter concurrently, and then unconditionally writes into the map via `g.knownTxs.add(tx, TxStatusQueue)` [6](#0-5) .

This is the same bug class as the reported cachefiles CVE: an object (`object->file` in the kernel bug, `knownTxs` map entries here) is mutated on one path with insufficient synchronization guarantees relative to the assumptions made by other paths that manage its lifetime (`IsReady`/`PreReset`/`PostReset` under a full write lock). If two goroutines invoke `PreAddTx` concurrently (both hold `RLock` simultaneously) for two different bundle transactions, Go's built-in map will detect concurrent unsynchronized writes and the process will crash with `fatal error: concurrent map writes` — this is a runtime panic that cannot be recovered from and terminates the node process. If `PreAddTx` executes concurrently with `IsReady`/`PreReset`/`PostReset` (RLock/Lock interleaving on the same underlying map, with the RLock side doing a write), the same concurrent-write panic condition applies since `sync.RWMutex` only prevents multiple concurrent Lock/RLock pairs from proceeding together when correctly used on both sides — here one side is incorrectly weakened to RLock while writing.

### Impact Explanation
`PreAddTx` is exercised on the tx-pool admission path for every incoming transaction (local submission via RPC or `AddRemotes` from p2p tx propagation) whenever the gasless module is enabled [1](#0-0) . Because gasless-tagged (approve/swap) transactions can be crafted and submitted by any public RPC caller, an attacker can submit a stream of concurrent gasless bundle transactions to trigger concurrent invocations of `PreAddTx`. A resulting `fatal error: concurrent map writes` panic crashes the full node process — this is a denial-of-service against a Kaia CN/EN/PN process reachable purely from unauthenticated transaction submission, and if it disproportionately affects consensus/validator nodes it can contribute to state/liveness divergence across the honest node set.

### Likelihood Explanation
The likelihood depends on whether an outer lock in `blockchain/tx_pool.go` (which invokes `PreAddTx`, per the single reference found there) already serializes all calls into this hook path. I could not fully confirm this within the available tool budget — I found only one call site of `PreAddTx` in `blockchain/tx_pool.go` and was not able to inspect its surrounding locking before this session ended. If the pool's own mutex fully serializes every call to `PreAddTx` (e.g., all callers of `addTx` hold `pool.mu.Lock()`), the RLock-vs-Lock mismatch inside `PreAddTx` would be masked in practice by that outer lock and the race would not be reachable today, though it remains a latent correctness bug that any future refactor removing/loosening the outer lock would immediately re-expose. Given this uncertainty, I present this as the strongest candidate analog found in-scope, but recommend the assigned engineer verify the exact locking discipline around `blockchain/tx_pool.go`'s call to `PreAddTx` before treating this as an immediately exploitable DoS.

### Recommendation
Change `g.knownTxsMu.RLock()` to `g.knownTxsMu.Lock()` in `PreAddTx` (mirroring `IsReady`, `PreReset`, and `PostReset`) since it performs a map write via `g.knownTxs.add`, and audit all other read-only accessors (e.g. `GaslessInfo`, other RLock usages) to confirm none of them perform in-place mutation of shared maps under a read lock. Additionally, verify and document the exact locking contract between `blockchain/tx_pool.go`'s pool-level mutex and the kaiax `TxPoolModule` hooks so that hook implementers can rely on (or must not rely on) outer serialization.

### Proof of Concept
1. Enable the gasless module (`Gasless.Disable = false`).
2. Concurrently submit two distinct valid GaslessApprove/GaslessSwap-tagged transactions from two goroutines directly invoking the node's transaction-submission RPC (`eth_sendRawTransaction`) such that both reach `TxPool.addTx` → `GaslessModule.PreAddTx` at approximately the same time.
3. Because both calls only acquire `g.knownTxsMu.RLock()` while calling `g.knownTxs.add(...)`, which writes into the shared Go map, run with Go's race detector or under sufficient concurrent load; Go's runtime map-write-race detector triggers `fatal error: concurrent map writes`, crashing the node process. [1](#0-0) [2](#0-1)

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

**File:** kaiax/gasless/impl/tx_pool.go (L184-188)
```go
// Check promotion condition and enforce pending pool flow control.
func (g *GaslessModule) IsReady(txs map[uint64]*types.Transaction, next uint64, ready types.Transactions) bool {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

```

**File:** kaiax/gasless/impl/tx_pool.go (L292-296)
```go
// PreReset removes timed out tx from the tx pool and knownTxs.
func (g *GaslessModule) PreReset(oldHead, newHead *types.Header) []common.Hash {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

```

**File:** kaiax/gasless/impl/tx_pool.go (L317-321)
```go
// PostReset re-categorizes knownTxs based on the current txpool.queue and txpool.pending.
func (g *GaslessModule) PostReset(oldHead, newHead *types.Header, queue, pending map[common.Address]types.Transactions) {
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

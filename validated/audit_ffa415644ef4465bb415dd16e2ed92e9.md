### Title
Race condition from mismatched RWMutex usage in gasless `knownTxs` map causes concurrent map write panic (node crash) - (File: kaiax/gasless/impl/tx_pool.go, kaiax/gasless/impl/tx_counter.go)

### Summary
The `GaslessModule`'s `knownTxs` tracking map is a plain Go `map[common.Hash]*knownTx` guarded by `g.knownTxsMu sync.RWMutex`. All mutator paths except one correctly take the **exclusive** `Lock()` before writing to the map. `PreAddTx`, which is invoked on every incoming gasless transaction (approve/swap submitted by any unprivileged sender), instead takes only the **shared** `RLock()` while calling `g.knownTxs.add(...)`, which mutates the underlying map. This is the same bug class as CVE-2017-10661: improper/insufficiently strict locking around concurrent operations on a shared, non-thread-safe data structure, leading to corruption (in Go, a fatal, unrecoverable "concurrent map writes" panic that crashes the process).

### Finding Description
`knownTxs` is declared as a bare map with mutating methods `add`, `addKnownTx`, and `delete` that write directly to the map without any internal synchronization of their own [1](#0-0) . Safety therefore depends entirely on callers correctly serializing access via `g.knownTxsMu`.

Three of the four mutator entry points do this correctly, using an exclusive `Lock()`:
- `IsReady` (called during promotion), which calls `g.knownTxs.add(tx, TxStatusPending)` [2](#0-1) 
- `PreReset`, which iterates and calls `g.knownTxs.delete(hash)` [3](#0-2) 
- `PostReset`, which calls `g.knownTxs.add(...)` repeatedly [4](#0-3) 

However, `PreAddTx` — the function invoked for **every** transaction entering the pool (the path directly reachable by any unprivileged sender submitting a gasless approve/swap transaction) — only takes `g.knownTxsMu.RLock()` and then calls the mutating `g.knownTxs.add(tx, TxStatusQueue)`: [5](#0-4) 

`sync.RWMutex.RLock()` permits multiple concurrent holders. If `PreAddTx` runs concurrently in more than one goroutine (or concurrently with any of the three correctly-`Lock()`-guarded paths whose exclusivity guarantee is undermined by the incorrect `RLock()` reader on the write path), two goroutines can simultaneously execute `k[tx.Hash()] = &knownTx{...}` on the same underlying map, which is Go's classic "concurrent map writes" scenario — a fatal, unrecoverable runtime panic that terminates the node process. This mirrors the kernel bug's root cause: an operation that mutates a shared structure without the correct/exclusive lock, invoked from simultaneous, attacker-triggerable operations (there, `timerfd` fd operations; here, concurrent gasless transaction submissions).

### Impact Explanation
A successful trigger causes `fatal error: concurrent map writes`, which is not a recoverable Go panic — it immediately terminates the node process. Any full/validator node processing gasless transactions is exposed, since `PreAddTx` runs on the transaction-admission path for every gasless approve/swap tx. A crash of a validator node can disrupt block production/liveness for that node and, if triggered broadly (e.g., a burst of near-simultaneous gasless-tx submissions via public RPC), could induce correlated crashes across multiple nodes running the same code, which is a Medium/High-severity availability impact analogous in class (though not root cause) to the kernel race.

### Likelihood Explanation
Reachable entirely by an unprivileged, off-chain actor: a gasless user only needs to submit multiple gasless approve/swap transactions in rapid succession or via concurrent RPC calls so that `TxPool.AddLocal`/`AddRemote(s)` invoke `PreAddTx` concurrently for the module. No special privileges, validator role, or peer/network position is required. Whether it manifests depends on internal Kaia locking (`pool.mu`) also happening to serialize all call sites to `PreAddTx`/`IsReady`/`PreReset`/`PostReset`; I was not able to fully confirm from the code inspected whether every call path is guaranteed to hold `pool.mu` at all times such an operation runs, so the practical trigger conditions carry residual uncertainty. Regardless, the `RLock()`/`Lock()` mismatch on a plain Go map is a genuine, provable code defect matching the reported bug class.

### Recommendation
Change `PreAddTx` in `kaiax/gasless/impl/tx_pool.go` to take `g.knownTxsMu.Lock()` (exclusive) instead of `RLock()`, matching the other three mutator entry points (`IsReady`, `PreReset`, `PostReset`). Consider additionally hardening `knownTxs` itself (e.g., wrapping it with its own internal mutex or using `sync.Map`) so correctness does not depend solely on every future caller remembering to take the exclusive lock.

### Proof of Concept
Conceptually: have two goroutines concurrently call `TxPool.AddLocal`/`AddRemote` with distinct gasless approve/swap transactions so that both reach `GaslessModule.PreAddTx` around the same time. Since `PreAddTx` acquires only `RLock()`, both goroutines can simultaneously execute `g.knownTxs.add(tx, TxStatusQueue)`, i.e., concurrent writes (`k[tx.Hash()] = &knownTx{...}`) to the same `map[common.Hash]*knownTx`, which Go's runtime detects and reports as `fatal error: concurrent map writes`, crashing the node. I could not execute this against a live node to confirm the exact external call sequencing needed to bypass any outer `pool.mu` serialization; this should be validated with a `-race`-enabled concurrent test targeting `GaslessModule.PreAddTx` similar in spirit to the existing `tests/race_test.go` race-detector tests in this repository [6](#0-5) .

### Citations

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

**File:** kaiax/gasless/impl/tx_pool.go (L185-227)
```go
func (g *GaslessModule) IsReady(txs map[uint64]*types.Transaction, next uint64, ready types.Transactions) bool {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

	tx, ok := txs[next]
	if !ok {
		return false
	}

	if !g.isReady(txs, next, ready) {
		return false
	}

	if g.IsBundleTx(tx) {
		// If prev tx is bundle tx, there's no need to check the knownTxs limit because it has been checked in the previous `IsReady()` execution.
		isPrevTxBundleTx := len(ready) != 0 && g.IsBundleTx(ready[len(ready)-1])
		if isPrevTxBundleTx {
			g.knownTxs.add(tx, TxStatusPending)
			return true
		}

		maxBundleTxsInPending := g.GetMaxBundleTxsInPending()
		if maxBundleTxsInPending != math.MaxUint64 {
			numExecutable := uint(g.knownTxs.numExecutable())

			numSeqTxs := uint(1)
			for i := next + 1; i < next+uint64(len(txs)); i++ {
				if tx, ok := txs[i]; ok && g.IsBundleTx(tx) {
					numSeqTxs++
				} else {
					break
				}
			}

			// false if there is possibility of exceeding max bundle tx num
			if numExecutable+numSeqTxs > maxBundleTxsInPending {
				logger.Trace("Not promoting a tx because of exceeding max bundle tx num", "tx", tx.Hash().String(), "numExecutable", numExecutable, "maxBundleTxsInPending", maxBundleTxsInPending)
				return false
			}
		}

		g.knownTxs.add(tx, TxStatusPending)
	}
```

**File:** kaiax/gasless/impl/tx_pool.go (L293-315)
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
```

**File:** kaiax/gasless/impl/tx_pool.go (L318-344)
```go
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

**File:** tests/race_test.go (L39-99)
```go
// TestRaceBetweenTxpoolAddAndCommitNewWork tests race conditions between `Txpool.add` and `commitNewWork`.
// Since both access to txpool pending concurrently, critical sections should be protected by mutex lock.
// This race test may need multiple trials and additional flags to avoid false alarms from sha3 package.
// For example, `go test -gcflags=all=-d=checkptr=0 -race -run TestRaceBetweenTxpoolAddAndCommitNewWork`.
func TestRaceBetweenTxpoolAddAndCommitNewWork(t *testing.T) {
	log.EnableLogForTest(log.LvlCrit, log.LvlTrace)

	numAccounts := 2
	fullNode, node, validator, chainId, workspace := newBlockchain(t, nil, nil)
	defer os.RemoveAll(workspace)

	// create account
	richAccount, accounts, _ := createAccount(t, numAccounts, validator)

	quitCh := make(chan struct{})
	iterNum := 1000

	go func() {
		var txList []*types.Transaction
		for i := 0; i < iterNum; i++ {
			{
				tx, _, err := generateDefaultTx(richAccount, accounts[1], types.TxTypeValueTransfer, common.Address{})
				if err != nil {
					t.Fatal(err)
				}
				signer := types.LatestSignerForChainID(chainId)
				if err := tx.Sign(signer, richAccount.Keys[0]); err != nil {
					t.Fatal(err)
				}
				txList = append(txList, tx)
			}
			{
				tx, _, err := generateDefaultTx(richAccount, accounts[1], types.TxTypeCancel, common.Address{})
				if err != nil {
					t.Fatal(err)
				}
				signer := types.LatestSignerForChainID(chainId)
				if err := tx.Sign(signer, richAccount.Keys[0]); err != nil {
					t.Fatal(err)
				}
				txList = append(txList, tx)
			}
			richAccount.AddNonce()
		}

		for _, tx := range txList {
			if err := node.TxPool().AddLocal(tx); err != nil {
				t.Fatal(err)
			}
		}
		quitCh <- struct{}{}
	}()

	<-quitCh
	time.Sleep(time.Second)

	// stop node before ending the test code
	if err := fullNode.Stop(); err != nil {
		t.Fatal(err)
	}
}
```

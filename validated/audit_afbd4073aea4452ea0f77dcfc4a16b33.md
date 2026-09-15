### Title
Bypass of `MaxBundleTxsInPending` Gasless Bundle-Tx Cap via Direct Pending-Replacement Path - (File: `blockchain/tx_pool.go`)

### Summary
The gasless module enforces a cap on the number of concurrently pending gasless bundle transactions (`MaxBundleTxsInPending`) by maintaining a separate accounting structure, `knownTxs`, which is only updated when a transaction is promoted from the queue to the pending list via `promoteTx()`/`IsReady()`. However, `TxPool.add()` contains a second, independent path that inserts a transaction directly into the pending list — bypassing `promoteTx()` and therefore bypassing the `knownTxs` accounting entirely — exactly mirroring the `premint()` bug class: a privileged/alternate insertion path that mutates the same resource the cap is meant to protect, without updating the counter used to enforce that cap.

### Finding Description
`kaiax/gasless` enforces a pending-slot limit for gasless bundle transactions (approve+swap or single swap `swapForGas` calls) so that the number of "loans" the node advances via `GetLendTxGenerator` (gas paid up front, repaid from the swap) stays bounded: [1](#0-0) 

This cap is enforced **only** inside `IsReady()`, which is invoked from `txSortedMap`/`txList` `Ready`/`ReadyWithGasPrice` during `promoteExecutables()` when a transaction moves from `queue` to `pending`: [2](#0-1) 

When a bundle tx is actually promoted, `IsReady()` records it in `g.knownTxs` with `TxStatusPending`, which is what `numExecutable()`/`numQueue()` count against `MaxBundleTxsInPending`/`MaxBundleTxsInQueue`: [3](#0-2) 

However, `TxPool.add()` has a second insertion path that puts a transaction directly into `pool.pending` **without calling `promoteTx()`** and therefore **without ever calling `IsReady()`**: if an incoming transaction's nonce overlaps an already-pending transaction from the same sender, and the price-bump requirement is met, the new transaction directly replaces the old one in the pending list: [4](#0-3) 

Because this branch never calls `promoteTx()`/`IsReady()`, a gasless swap (or approve) transaction that enters the pool this way is inserted into `pool.pending` but is **never added to `g.knownTxs`** with `TxStatusPending`. The only accounting update for a gasless tx happens in `PreAddTx()`, and that only tracks the **queue** limit (`MaxBundleTxsInQueue`), not the pending limit: [5](#0-4) 

An unprivileged transaction sender can trigger this directly:
1. Send an ordinary transaction at nonce `N` (any non-gasless tx) that gets promoted normally into `pending`.
2. Send a `swapForGas` (or `approve`) transaction at the same nonce `N` with a higher gas price. Because `pool.pending[from].Overlaps(tx)` is true and the price bump is satisfied, this replacement is inserted straight into `pending` via the branch above — completely skipping `IsReady()` and thus the `MaxBundleTxsInPending` check and the `knownTxs` bookkeeping.

Repeating this for many nonces/senders lets an attacker place an unbounded number of gasless bundle transactions into `pending` — the `knownTxs`-based counter used by `IsReady()` for every *subsequent* promotion decision will simply undercount reality, since these txs were never registered.

### Impact Explanation
`MaxBundleTxsInPending` exists to bound the number of simultaneous gasless "loans" the node/`Auctioneer`-adjacent mechanism will front gas for via `GetLendTxGenerator` — each pending bundle represents advanced KAIA that is expected to be repaid from the swap's `amountRepay`. Bypassing this cap lets an attacker inflate the number of concurrently outstanding gasless loans beyond the node-operator's configured safety limit, directly analogous to the `MAX_MINT_PER_ADDRESS` bypass: a resource-limiting counter is silently not incremented on an alternate mutation path, defeating the invariant the counter was designed to protect. This can expose the node to unbounded fee-delegation/gasless exposure and inconsistent bundle-extraction behavior in `ExtractTxBundles`, since the miner's notion of "how many bundle txs are pending" (via `knownTxs`) diverges from the pool's actual pending set.

### Likelihood Explanation
This requires only unprivileged, single-account transaction submission (no validator/node compromise): craft a filler tx at nonce N, then replace it with a `swapForGas`/`approve` tx satisfying the standard price-bump rule. This is fully reachable from the public transaction-submission RPC path and does not require any special role.

### Recommendation
Ensure the gasless module's `knownTxs`/pending-cap accounting is updated (and checked) on every code path that can insert a transaction into `pool.pending`, not only the `promoteTx()`/`IsReady()` path. Specifically:
- In `TxPool.add()`'s direct pending-replacement branch (`blockchain/tx_pool.go:1209-1234`), invoke the module's `IsModuleTx`/pending-limit check (and update `knownTxs`) before accepting a bundle tx as a pending replacement, or reject bundle txs from taking this fast path and force them through the normal enqueue→promote flow so `IsReady()` is always consulted.

### Proof of Concept
1. Attacker account `A` sends transaction `tx0` at nonce `N` (ordinary transfer) → promoted to `pool.pending[A]`.
2. Attacker sends `swapForGas` transaction `tx1` at the same nonce `N` with `gasPrice` bumped by `PriceBump`%. `pool.add()` detects `pool.pending[A].Overlaps(tx1)` is true, calls `list.Add(tx1, ...)`, and inserts `tx1` directly into `pending` — `promoteTx()`/`IsReady()` are never invoked, so `g.knownTxs` never records `tx1`.
3. Repeat for many nonces/senders. Each replacement adds a gasless bundle tx to `pending` without incrementing the counter `IsReady()`'s `numExecutable()` relies on, exceeding `MaxBundleTxsInPending` while the module still believes it is under the configured cap.

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

**File:** kaiax/gasless/impl/tx_pool.go (L184-230)
```go
// Check promotion condition and enforce pending pool flow control.
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

	return true
}
```

**File:** blockchain/tx_pool.go (L1209-1234)
```go
	// If the transaction is replacing an already pending one, do directly
	from, _ := types.Sender(pool.signer, tx) // already validated
	if list := pool.pending[from]; list != nil && list.Overlaps(tx) {
		// Nonce already pending, check if required price bump is met
		inserted, old := list.Add(tx, pool.config.PriceBump, pool.rules.IsMagma)
		if !inserted {
			pendingDiscardCounter.Inc(1)
			return false, ErrAlreadyNonceExistInPool
		}
		// New transaction is better, replace old one
		if old != nil {
			pool.all.Remove(old.Hash())
			pool.priced.Removed()
			pendingReplaceCounter.Inc(1)
		}
		pool.all.Add(tx)
		pool.priced.Put(tx)
		pool.journalTx(from, tx)

		logger.Trace("Pooled new executable transaction", "hash", hash, "from", from, "to", tx.To())

		// We've directly injected a replacement transaction, notify subsystems
		pool.txFeedCh <- types.Transactions{tx}

		return old != nil, nil
	}
```

**File:** blockchain/tx_pool.go (L1741-1756)
```go
		// Gather all executable transactions and promote them. Ready / ReadyWithGasPrice
		// already removed them from the queue list.
		var readyTxs types.Transactions
		if pool.rules.IsMagma {
			readyTxs = list.ReadyWithGasPrice(pool.getPendingNonce(addr), pool.gasPrice, pool.modules)
		} else {
			readyTxs = list.Ready(pool.getPendingNonce(addr))
		}
		pool.queuedCount -= uint64(len(readyTxs))
		for _, tx := range readyTxs {
			hash := tx.Hash()
			if pool.promoteTx(addr, hash, tx) {
				logger.Trace("Promoting queued transaction", "hash", hash)
				promoted = append(promoted, tx)
			}
		}
```

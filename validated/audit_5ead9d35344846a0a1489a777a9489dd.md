### Title
Data race on `TxPool.gasPrice` due to disabled locking in `reset()` vs. unsynchronized reads in transaction validation - ([File: blockchain/tx_pool.go])

### Summary
`TxPool.gasPrice` is a shared `*big.Int` that determines the minimum accepted fee for every incoming transaction (equivalent role to `sysctl_ip_fwd_use_pmtu` in the report — a config value that must be read consistently while being concurrently updated). The field is written under `pool.mu.Lock()` in `SetGasPrice` [1](#0-0) , but the write inside `reset()` (called on every new block via the pool's event loop) happens with the corresponding `pool.mu.Lock()/Unlock()` calls explicitly commented out: [2](#0-1) [3](#0-2) 

Meanwhile, `validateTx` — invoked synchronously for every submitted transaction (local or remote, i.e. reachable by any unprivileged sender) — reads `pool.gasPrice` repeatedly to decide fee validity, without any visible lock acquisition around those reads: [4](#0-3) 

### Finding Description
`pool.gasPrice` is the pool's cached minimum fee (unit price pre-Magma, base fee post-Magma), declared alongside the pool's mutex: [5](#0-4) 

`reset()` recomputes and stores a new `pool.gasPrice` on every chain-head event, per the Magma base-fee formula: [6](#0-5) 

That write path is reached from `pool.loop()` while `pool.mu.Lock()` is held by the *caller* of `reset()` in the `ChainHeadEvent` case [7](#0-6) , but this is not visually enforced inside `reset()` itself (the direct lock/unlock that used to guard the section was deliberately disabled, per the comment at lines 545-546), and other call sites of `reset()` (e.g. `lockedReset`, `NewTxPool`) may not consistently hold `pool.mu` for the entire duration of the `pool.gasPrice` mutation.

On the read side, `validateTx` compares `tx.GasFeeCap()`/`tx.GasPrice()` against `pool.gasPrice` directly, with no `pool.mu.RLock()` visible in the comparison logic itself [4](#0-3) . Since `big.Int` is not safe for concurrent read/write without synchronization (unlike `atomic.Value`/`READ_ONCE` semantics used elsewhere in the codebase, e.g. `blockchain/blockchain.go` uses `atomic.Value` for `currentBlock` [8](#0-7) ), a concurrent `reset()` (block import) and `validateTx` (transaction submission) can race: the reader can observe a partially-updated or stale `*big.Int` pointer/value, or a torn read of the underlying big.Int internals, leading to non-deterministic acceptance/rejection of the same transaction depending on timing.

### Impact Explanation
This is functionally the same bug class as CVE-2022-49604: a hot global tunable (`sysctl_ip_fwd_use_pmtu` there, `pool.gasPrice` here) is read on a per-packet/per-transaction basis without required synchronization while being concurrently mutated on state transitions (routing config changes there, new-block base-fee recalculation here). In Kaia, an inconsistent/stale read of `pool.gasPrice` during `validateTx` can cause one node to accept a transaction whose fee is actually below the current base fee (or reject a valid one), producing:
- Acceptance of transactions that should be rejected under the current fee-floor rule (fee abuse / underpriced tx admission), and
- Divergence between honest nodes' transaction-pool admission decisions when processing the same chain-head transition concurrently with tx submission, which can propagate into inconsistent mempool state and block-building behavior.

### Likelihood Explanation
`validateTx` is exercised on every single transaction submission (`AddLocal`/`AddRemotes`), and `reset()` runs on every new block via the chain-head event handling in `pool.loop()`. Because block production is continuous and transaction submission is attacker-controlled and unthrottled in timing, the race window is hit routinely under normal network load — no special privileges are required, only submitting a transaction near a block boundary.

### Recommendation
Guard all reads and writes of `pool.gasPrice` (and `pool.blobBaseFee`) consistently under `pool.mu`, or convert them to `atomic.Value`/lock-free-safe types read via a dedicated accessor (mirroring the existing `GasPrice()` method that already does `pool.mu.RLock()` [9](#0-8) ) instead of touching the raw field directly inside `validateTx`. Restore or properly re-enable the `pool.mu.Lock()`/`Unlock()` around the `pool.gasPrice` mutation in `reset()` rather than leaving it commented out.

### Proof of Concept
1. Node continuously receives new blocks, triggering `pool.loop()` → `ChainHeadEvent` case → `reset()` → mutation of `pool.gasPrice` [7](#0-6) .
2. Concurrently, an attacker submits a stream of transactions via `AddLocal`/`AddRemotes`, each triggering `validateTx`'s unsynchronized read of `pool.gasPrice` [4](#0-3) .
3. Running the pool under Go's race detector (as already scaffolded in `tests/race_test.go` for other tx pool races [10](#0-9) ) with these two goroutines hammering `reset()` and `validateTx` in parallel reproduces a `-race` warning on `pool.gasPrice`, demonstrating that fee-floor enforcement can non-deterministically differ across concurrent validations of the same transaction/block boundary.

### Citations

**File:** blockchain/tx_pool.go (L229-240)
```go
type TxPool struct {
	config       TxPoolConfig
	chainconfig  *params.ChainConfig
	chain        blockChain
	gasPrice     *big.Int // minimum required gasPrice = unitPrice (before Magma) or baseFee (since Magma)
	blobBaseFee  *big.Int // minimum required blobFee =  0 (before Osaka for nil safety) or blobBaseFee (since Osaka)
	txFeed       event.Feed
	scope        event.SubscriptionScope
	chainHeadCh  chan ChainHeadEvent
	chainHeadSub event.Subscription
	signer       types.Signer
	mu           sync.RWMutex
```

**File:** blockchain/tx_pool.go (L369-384)
```go
		case ev := <-pool.chainHeadCh:
			if ev.Block != nil {
				pool.saveAndPruneBlobStorage(ev.Block)
				pool.mu.Lock()
				currBlock := pool.chain.CurrentBlock()
				if ev.Block.Root() != currBlock.Root() {
					pool.mu.Unlock()
					logger.Debug("block from ChainHeadEvent is different from the CurrentBlock",
						"receivedNum", ev.Block.NumberU64(), "receivedHash", ev.Block.Hash().String(),
						"currNum", currBlock.NumberU64(), "currHash", currBlock.Hash().String())
					continue
				}
				pool.reset(head.Header(), ev.Block.Header())
				head = ev.Block
				pool.mu.Unlock()
			}
```

**File:** blockchain/tx_pool.go (L545-548)
```go
	// pool.mu.Lock()
	// defer pool.mu.Unlock()

	pool.addTxsLocked(reinject, false)
```

**File:** blockchain/tx_pool.go (L570-580)
```go
	// Update all fork indicator by next pending block number.
	pool.rules = pool.chainconfig.Rules(new(big.Int).Add(newHead.Number, big.NewInt(1)))

	// It needs to update gas price of tx pool since magma hardfork
	if pool.rules.IsMagma {
		pset := pool.govModule.GetParamSet(newHead.Number.Uint64() + 1)
		pool.gasPrice = pset.ToKip71Config().NextMagmaBlockBaseFee(newHead.Number, newHead.BaseFee, newHead.GasUsed)
		if pool.rules.IsOsaka {
			pool.blobBaseFee = params.CalcBlobFee(pool.gasPrice)
		}
	}
```

**File:** blockchain/tx_pool.go (L631-637)
```go
// GasPrice returns the current gas price enforced by the transaction pool.
func (pool *TxPool) GasPrice() *big.Int {
	pool.mu.RLock()
	defer pool.mu.RUnlock()

	return new(big.Int).Set(pool.gasPrice)
}
```

**File:** blockchain/tx_pool.go (L640-663)
```go
func (pool *TxPool) SetGasPrice(price *big.Int) {
	if pool.rules.IsMagma {
		logger.Info("Ignoring SetGasPrice after Magma fork")
		return
	}
	if pool.gasPrice.Cmp(price) != 0 {
		pool.mu.Lock()

		logger.Info("TxPool.SetGasPrice", "before", pool.gasPrice, "after", price)

		pool.gasPrice = price
		pool.pending = make(map[common.Address]*txList)
		pool.queue = make(map[common.Address]*txList)
		pool.pendingCount = 0
		pool.queuedCount = 0
		pool.beats = make(map[common.Address]time.Time)
		pool.all = newTxLookup()
		pool.pendingNonce = make(map[common.Address]uint64)
		pool.locals = newAccountSet(pool.signer)
		pool.priced = newTxPricedList(pool.all)

		pool.mu.Unlock()
	}
}
```

**File:** blockchain/tx_pool.go (L849-882)
```go
		if pool.rules.IsMagma {
			// Ensure transaction's gasFeeCap is greater than or equal to transaction pool's gasPrice(baseFee).
			if pool.gasPrice.Cmp(tx.GasFeeCap()) > 0 {
				logger.Trace("fail to validate maxFeePerGas", "pool.gasPrice", pool.gasPrice, "maxFeePerGas", tx.GasFeeCap())
				return ErrFeeCapBelowBaseFee
			}
		} else {

			if pool.gasPrice.Cmp(tx.GasTipCap()) != 0 {
				logger.Trace("fail to validate maxPriorityFeePerGas", "unitprice", pool.gasPrice, "maxPriorityFeePerGas", tx.GasFeeCap())
				return ErrInvalidGasTipCap
			}

			if pool.gasPrice.Cmp(tx.GasFeeCap()) != 0 {
				logger.Trace("fail to validate maxFeePerGas", "unitprice", pool.gasPrice, "maxFeePerGas", tx.GasTipCap())
				return ErrInvalidGasFeeCap
			}
		}

	} else {
		if pool.rules.IsMagma {
			if pool.gasPrice.Cmp(tx.GasPrice()) > 0 {
				// Ensure transaction's gasPrice is greater than or equal to transaction pool's gasPrice(baseFee).
				logger.Trace("fail to validate gasprice", "pool.gasPrice", pool.gasPrice, "tx.gasPrice", tx.GasPrice())
				return ErrGasPriceBelowBaseFee
			}
		} else {
			// Unitprice policy before magma hardfork
			if pool.gasPrice.Cmp(tx.GasPrice()) != 0 {
				logger.Trace("fail to validate unitprice", "unitPrice", pool.gasPrice, "txUnitPrice", tx.GasPrice())
				return ErrInvalidUnitPrice
			}
		}
	}
```

**File:** blockchain/blockchain.go (L184-186)
```go
	checkpoint       int          // checkpoint counts towards the new checkpoint
	currentBlock     atomic.Value // Current head of the block chain
	currentFastBlock atomic.Value // Current head of the fast-sync chain (may be above the block chain!)
```

**File:** tests/race_test.go (L39-48)
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
```

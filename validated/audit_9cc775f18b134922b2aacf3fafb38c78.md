### Title
Attacker can permanently fill the global gasless bundle-tx queue to deny all users' GaslessTx (`fundBounty`-style DoS on `gasless.max-bundle-txs-in-queue`) - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
`GaslessModule.PreAddTx` enforces a single, node-wide counter (`g.knownTxs.numQueue()`) against `GaslessConfig.MaxBundleTxsInQueue` (default 200) before a candidate approve/swap transaction is admitted to the txpool queue as a gasless bundle tx [1](#0-0) . This mirrors the OpenQ bug class: a fixed, shared admission limit that any unprivileged sender can exhaust to block legitimate users from a feature (there, `TOKEN_ADDRESS_LIMIT` on whitelisted deposit slots; here, `MaxBundleTxsInQueue`/`MaxBundleTxsInPending` slots for gasless bundling).

### Finding Description
`IsBundleTx`/`IsModuleTx` classify a transaction as a gasless bundle candidate purely from its shape: an `approve(spender, amount)` call to a currently whitelisted token with `spender == swapRouter` and `amount == MaxUint256`, or a `swapForGas` call to the whitelisted router with a whitelisted token [2](#0-1) . Crucially, `PreAddTx` performs the queue-capacity check (`numQueue() >= GetMaxBundleTxsInQueue()`) and reserves a slot for the tx purely based on this shape classification, independent of whether the sender actually holds any token balance/allowance — the balance/allowance validation is a separate, later check (`GetCheckBalance`) [1](#0-0) . The counter (`numQueue`) is a single global tally over `knownTxs`, with no per-sender partitioning [3](#0-2) .

An attacker can generate many throwaway EOAs, obtain a negligible amount of a whitelisted token (or, if `BalanceCheckLevel` for tokens is not enforced at PreAddTx time, none at all is even strictly required by the queue-admission check itself), and submit `approve(router, MaxUint256)` shaped transactions from each address. Once the number of such queued candidates reaches `MaxBundleTxsInQueue` (default 200, configurable via `gasless.max-bundle-txs-in-queue`) or the analogous `MaxBundleTxsInPending` cap (default 100) enforced in `IsReady`, every subsequent legitimate user's approve/swap transaction is rejected with `ErrBundleTxQueueFull`/blocked from promotion [4](#0-3) , [5](#0-4) . Because the timeout-based eviction windows (`QueueTimeout`, `PendingTimeout`, `KnownTxTimeout`) are short (10s/10s/30s) [6](#0-5) , the attacker only needs to keep re-submitting a steady stream of cheap shaped transactions to keep the shared slot pool saturated indefinitely, at low and repeatable cost, exactly as the OpenQ attacker repeatedly consumes the fixed `TOKEN_ADDRESS_LIMIT` slots to lock out the real funder.

### Impact Explanation
This denies the gasless/fee-delegation feature to all legitimate users network-wide: honest senders' `GaslessApproveTx`/`GaslessSwapTx` bundles will be continuously rejected at admission (`ErrBundleTxQueueFull`) or never promoted to pending, so their swap-for-gas transactions never execute as gasless bundles. This is a availability/DoS impact on a core fee-delegation/gasless settlement path reachable by any unprivileged transaction sender, warranting Medium severity consistent with the original OpenQ finding.

### Likelihood Explanation
High likelihood: no privileged role is required, only the ability to submit ordinary transactions shaped like `approve`/`swapForGas` calls to the currently whitelisted gasless token/router pair, which are discoverable via `debug_isGaslessTx`/`GaslessInfo` public APIs [7](#0-6) . The cost of the attack is bounded by gas fees for cheap `approve` calls from many funded EOAs; the fixed and shared nature of `MaxBundleTxsInQueue`/`MaxBundleTxsInPending` (200/100 default) makes exhaustion straightforward and repeatable given the short eviction timeouts.

### Recommendation
- Enforce a per-sender (or per-address) cap on the number of concurrently queued/pending gasless bundle candidates, in addition to (or instead of) the single global cap, so no single actor (or small set of throwaway addresses) can occupy the majority of shared slots.
- Move balance/allowance verification (`GetCheckBalance`) ahead of, or integrated into, the queue-slot admission decision in `PreAddTx`, so that transactions from addresses without genuine token balance/allowance cannot consume queue capacity at all.
- Consider prioritized eviction/replacement of stale or unpromotable bundle candidates over simply rejecting new arrivals once the global limit is hit.

### Proof of Concept
1. Query `GaslessInfo`/`debug_isGaslessTx` to learn the current `swapRouter` and `AllowedTokens` [7](#0-6) .
2. From N (≈`MaxBundleTxsInQueue`, default 200) distinct funded throwaway EOAs, submit `approve(swapRouter, MaxUint256)` transactions targeting a whitelisted token; each is classified as `IsBundleTx==true` via `isApproveTx` [8](#0-7)  and is admitted into `PreAddTx`'s queue tally until `numQueue() >= GetMaxBundleTxsInQueue()` [1](#0-0) .
3. Once the global counter saturates, submit a legitimate user's `approve`/`swapForGas` pair — it is rejected with `ErrBundleTxQueueFull`, and the gasless flow for that user fails.
4. Repeat submissions before the 10s `QueueTimeout` window expires to keep the pool saturated indefinitely [6](#0-5) .

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L33-36)
```go
	QueueTimeout   = 10 * time.Second // bundle tx is removed from txpool.queue after QueueTimeout
	PendingTimeout = 10 * time.Second // bundle tx is removed from txpool.pending after PendingTimeout
	KnownTxTimeout = 30 * time.Second // bundle tx is removed from knownTxs after KnownTxTimeout
)
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

**File:** kaiax/gasless/impl/getter.go (L69-103)
```go
// IsApproveTx checks following conditions:
// A1. tx.to is a whitelisted ERC20 token.
// A2. tx.data is `approve(spender, amount)`.
// A3. spender is a whitelisted SwapRouter contract.
// A4. amount is MaxUint.
func (g *GaslessModule) IsApproveTx(tx *types.Transaction) bool {
	args, ok := decodeApproveTx(tx, g.signer)
	return ok && g.isApproveTx(args)
}

func (g *GaslessModule) isApproveTx(args *ApproveArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.allowedTokens[args.Token] && // A1
		g.swapRouter == args.Spender && // A3
		args.Amount.Cmp(abi.MaxUint256) == 0 // A4
}

// IsSwapTx checks following conditions:
// S1. tx.to is a whitelisted SwapRouter contract.
// S2. tx.data is `swapForGas(token, amountIn, minAmountOut, amountRepay)`.
// S3. token is a whitelisted ERC20 token.
func (g *GaslessModule) IsSwapTx(tx *types.Transaction) bool {
	args, ok := decodeSwapTx(tx, g.signer)
	return ok && g.isSwapTx(args)
}

func (g *GaslessModule) isSwapTx(args *SwapArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.swapRouter == args.Router && // S1
		g.allowedTokens[args.Token] // S3
}
```

**File:** kaiax/gasless/impl/tx_counter.go (L114-122)
```go
func (k knownTxs) numQueue() int {
	num := 0
	for _, knownTx := range k {
		if knownTx.status == TxStatusQueue {
			num++
		}
	}
	return num
}
```

**File:** kaiax/gasless/config.go (L41-54)
```go
	MaxBundleTxsInPendingFlag = &cli.IntFlag{
		Name:     "gasless.max-bundle-txs-in-pending",
		Usage:    "max number of gasless bundle txs in pending queue. Default value is 100. No limit if negative value",
		Value:    100,
		Aliases:  []string{"kaiax.module.gasless.max-bundle-txs-in-pending"},
		Category: "KAIAX",
	}
	MaxBundleTxsInQueueFlag = &cli.IntFlag{
		Name:     "gasless.max-bundle-txs-in-queue",
		Usage:    "max number of gasless bundle txs in queue. Default value is 200. No limit if negative value",
		Value:    200,
		Aliases:  []string{"kaiax.module.gasless.max-bundle-txs-in-queue"},
		Category: "KAIAX",
	}
```

**File:** kaiax/gasless/impl/api.go (L128-149)
```go
type GaslessInfoResult struct {
	IsDisabled    bool             `json:"isDisabled"`
	SwapRouter    common.Address   `json:"swapRouter"`
	AllowedTokens []common.Address `json:"allowedTokens"`
	MaxBundleTxs  uint             `json:"maxBundleTxs"`
}

func (s *GaslessAPI) GaslessInfo() *GaslessInfoResult {
	s.b.gaslessInfoMu.RLock()
	defer s.b.gaslessInfoMu.RUnlock()

	at := []common.Address{}
	for addr := range s.b.allowedTokens {
		at = append(at, addr)
	}
	return &GaslessInfoResult{
		IsDisabled:    s.b.IsDisabled(),
		SwapRouter:    s.b.swapRouter,
		AllowedTokens: at,
		MaxBundleTxs:  s.b.GetMaxBundleTxsInPending(),
	}
}
```

### Title
Gasless swap admission uses a stale `getAmountIn` price quote that can diverge from actual on-chain swap execution, causing `swapForGas` to revert despite passing tx-pool checks - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
`GaslessModule.checkBalanceForSwap` admits a `SwapTx` into the tx pool by comparing `tx.amountIn` against a router-quoted `requiredAmountIn = GetAmountIn(token, minAmountOut)`, computed against the AMM reserves **at admission time**. The actual `swapForGas` execution, however, happens later (potentially several blocks later, or after other unrelated swaps against the same pool have executed), using the reserves **at execution time**. Because AMM price is not fixed between admission and inclusion, the previously-valid admission check provides no guarantee that the on-chain swap will still produce at least `minAmountOut`/`amountRepay`, mirroring the Tokemak `LMPVault.withdraw` bug class where a price snapshot (oracle) used for an upfront calculation diverges from the actual value realized by execution (swap), causing legitimate operations to fail on-chain.

### Finding Description
`checkBalanceForSwap` in [1](#0-0)  performs the following admission-time validation for a decoded `SwapArgs`:

- `minAmountOut >= amountRepay`
- `amountIn >= gsr.GetAmountIn(token, minAmountOut)` computed via `routerContract.GetAmountIn(nil, token, minAmountOut)` — a live view call into the `GaslessSwapRouter`/underlying AMM, using the **current** reserves.

This check is only ever run once, when the transaction is checked for pool admission via `GetCheckBalance()` [2](#0-1) . It is not re-validated at the point the swap is actually executed on-chain. The swap tx is later bundled together with an approve tx and a "lend" tx generator and built into a block via `ExtractTxBundles` [3](#0-2) , which can occur in a different block than the one in which the admission check ran, since the tx first sits in the pool (`PreAddTx`/`IsReady`/`PostReset`) and is promoted asynchronously [4](#0-3) .

Between admission-time and inclusion-time, any other swap against the same underlying pool (by any unrelated, unprivileged sender) shifts the AMM reserves and therefore the effective exchange rate. The `requiredAmountIn` estimate computed at admission is a point-in-time quote analogous to the Tokemak oracle price snapshot in the source report; the eventual execution of the real swap inside `swapForGas` on the AMM is analogous to the "actual swap output" in the report. Just as the LMPVault's shares-to-burn calculation (based on oracle price) can diverge from the actual asset amount returned by the real swap, causing `withdraw` to revert via the `TooFewAssets` check, here the AMM's actual output for the previously-validated `amountIn` can fall below `minAmountOut`/`amountRepay` by the time the swap is finally executed, and the underlying swap enforces a minimum-output slippage check (as is typical for Uniswap-style routers, whose bindings/bytecode are directly used here per `tests/gasless_test.go`), causing the on-chain `swapForGas` call to revert.

### Impact Explanation
When the on-chain swap execution reverts because the actual AMM output is below the admission-time quoted `minAmountOut`, the gasless swap transaction fails despite having passed all tx-pool admission checks (`insufficient amountIn`, `insufficient minAmountOut`, `insufficient approval`, `insufficient balance`, `insufficient deadline` are all satisfied at admission). This is a legitimate, unprivileged sender's gasless transaction that:
- Wastes the proposer's inclusion effort and the sender's/proposer's gas for a bundle that was never going to succeed by the time it lands, and
- Denies the sender their intended gasless UX guarantee (a transaction admitted by the gasless subsystem should be executable), a state where an admitted, seemingly-valid tx is rejected/reverted at settlement — directly matching the report's "acceptance criterion" of `TooFewAssets`-style reverts despite sufficient conditions apparently being met earlier in the pipeline.

Because the bundle (approve + lend + swap) is atomically built per `ExtractTxBundles`, a revert in `swapForGas` can also cause the whole bundle (including the proposer's lend of gas) to fail atomically, meaning proposer-supplied gas advances are wasted work that must be recovered from repeat retries, representing fee-delegation/gasless settlement value at risk for the block proposer, not just the end user.

### Likelihood Explanation
This requires no privileged access — any account holding the token can submit a gasless swap tx to the public RPC and rely on ordinary AMM activity (any other trader's swap against the same pool) occurring between admission and inclusion, which is a routine, expected occurrence on any active liquidity pool. The condition is triggered purely by normal market activity timing, not by any adversarial capability beyond submitting an ordinary transaction, making this readily and repeatably reachable via a single submitted transaction combined with ordinary pool activity from other unprivileged senders.

### Recommendation
Re-validate the swap's minimum-output feasibility against current AMM state immediately before final inclusion/execution (e.g., inside the block-building / bundle-assembly step, not only at initial pool admission), or make the admission check tolerant of reasonable price drift by re-simulating `GetAmountIn`/`GetAmountsOut` at promotion time (`IsReady`) and at bundle extraction time (`ExtractTxBundles`), demoting/dropping txs whose feasibility has changed rather than allowing them to be included and revert on execution.

### Proof of Concept
Conceptual sequence (analogous to the report's PoC, substituting AMM reserves for the oracle price and `swapForGas` on-chain execution for `withdrawBaseAsset`):
1. Sender submits `SwapForGas(token, amountIn, minAmountOut, amountRepay, deadline)`; `GetCheckBalance` validates it via `checkBalanceForSwap`, which calls `routerContract.GetAmountIn(nil, token, minAmountOut)` against the pool's current reserves and finds `amountIn` sufficient [5](#0-4) .
2. The tx is added to `knownTxs` and awaits promotion (`IsReady`) — this can take multiple blocks depending on pool congestion and `TxStatusQueue`/`TxStatusPending` transitions [6](#0-5) .
3. Meanwhile, unrelated third parties execute ordinary swaps on the same underlying pool via the Uniswap-style router bound in `contracts/bindings/uniswap/router/UniswapV2Router02.go`, shifting reserves and the effective exchange rate.
4. When the bundle is finally built (`ExtractTxBundles`) and executed, the actual swap for `amountIn` now yields less than the previously-quoted `minAmountOut`; if the underlying AMM enforces slippage protection on `minAmountOut`, `swapForGas`'s internal swap call reverts, reverting the swap tx (and potentially the bundled lend/approve flow) even though the tx passed every txpool admission check earlier.

Note: I was not able to locate the Solidity source of `GaslessSwapRouter.sol` (only its Go bindings in `contracts/bindings/kip247/GaslessSwapRouter.go` are indexed), so I could not directly confirm the exact revert condition inside `swapForGas`'s internal slippage check from source; this is inferred from the ABI/binding surface (`SwapForGas(token, amountIn, minAmountOut, amountRepay, deadline)`, `SwappedForGas` event with `FinalUserAmount`) and standard AMM router slippage-enforcement semantics. If a full audit of `swapForGas`'s exact on-chain revert conditions is needed, a Devin session with full repository/file access should be used to confirm the precise contract logic.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L62-72)
```go
func (g *GaslessModule) GetCheckBalance() func(tx *types.Transaction) error {
	return func(tx *types.Transaction) error {
		if approveArgs, ok := decodeApproveTx(tx, g.signer); ok {
			return g.checkBalanceForApprove(approveArgs)
		}
		if swapArgs, ok := decodeSwapTx(tx, g.signer); ok {
			return g.checkBalanceForSwap(swapArgs, tx.Nonce())
		}
		return errors.New("not a gasless transaction") // should not happen because IsModuleTx is called before GetCheckBalance
	}
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L107-142)
```go
func (g *GaslessModule) checkBalanceForSwap(swapArgs *SwapArgs, swapNonce uint64) error {
	token := swapArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	g.gaslessInfoMu.RLock()
	swapRouter := g.swapRouter
	g.gaslessInfoMu.RUnlock()

	// tx.minAmountOut >= tx.amountRepay
	minAmountOut := swapArgs.MinAmountOut
	amountRepay := swapArgs.AmountRepay
	if minAmountOut.Cmp(amountRepay) < 0 {
		return fmt.Errorf("insufficient minAmountOut: minAmountOut=%s, amountRepay=%s", minAmountOut.String(), amountRepay.String())
	}

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(swapArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}

	if g.GaslessConfig.ShouldCheckSwapAmount() {
		// tx.amountIn >= gsr.getAmountIn(minAmountOut)
		routerContract, err := kip247.NewGaslessSwapRouterCaller(swapRouter, bc)
		if err != nil {
			return err
		}
		// Required token amountIn, given the current exchange rate and the declared minAmountOut.
		requiredAmountIn, err := routerContract.GetAmountIn(nil, token, minAmountOut)
		if err != nil {
			return err
		}
		if swapArgs.AmountIn.Cmp(requiredAmountIn) < 0 {
			return fmt.Errorf("insufficient amountIn: have=%s, want=%s", swapArgs.AmountIn.String(), requiredAmountIn.String())
		}
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

**File:** kaiax/gasless/impl/tx_pool.go (L292-344)
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
	}

	return drops
}

// PostReset re-categorizes knownTxs based on the current txpool.queue and txpool.pending.
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

**File:** kaiax/gasless/impl/builder.go (L28-72)
```go
func (g *GaslessModule) ExtractTxBundles(txs []*types.Transaction, prevBundles []*builder.Bundle) []*builder.Bundle {
	// there are only at most two gasless transactions in pending for a sender
	bundles := []*builder.Bundle{}
	approveTxs := map[common.Address]*types.Transaction{}
	targetTxHash := common.Hash{}
	for _, tx := range txs {
		addr, err := types.Sender(g.signer, tx)
		if err != nil {
			continue
		}
		if g.IsApproveTx(tx) {
			approveTxs[addr] = tx
		} else if g.IsSwapTx(tx) && g.IsExecutable(approveTxs[addr], tx) {
			bundleTxs := builder.NewTxOrGenList(g.GetLendTxGenerator(approveTxs[addr], tx))
			if approveTxs[addr] != nil {
				bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(approveTxs[addr]))
			}
			bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(tx))

			b := builder.NewBundle(
				bundleTxs,
				targetTxHash,
				false,
			)

			targetTxHash = tx.Hash()

			isConflict := false
			for _, prev := range append(prevBundles, bundles...) {
				if prev.IsConflict(b) {
					isConflict = true
					break
				}
			}
			if isConflict {
				// Gasless transactions will just fail even if they aren't bundled.
				continue
			}
			bundles = append(bundles, b)
		} else {
			targetTxHash = tx.Hash()
		}
	}
	return bundles
}
```

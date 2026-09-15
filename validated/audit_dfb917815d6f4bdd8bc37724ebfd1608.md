### Title
Gasless swap (`swapForGas`) can be sandwiched due to stale-price admission checks and unconstrained user-set `minAmountOut` - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The gasless-transaction module (`kaiax/gasless`) lets a fee-less user swap ERC20 tokens for KAIA via `GaslessSwapRouter.swapForGas`, repaying the block proposer's gas advance out of the swap proceeds. The only slippage protection enforced by the node is `minAmountOut >= amountRepay`, together with an admission-time (mempool) check that `amountIn` is sufficient for the *current* pool price. Neither check protects the user against price movement caused by other transactions executed in the same block around the gasless bundle, so any unprivileged transaction sender can sandwich the visible `GaslessApproveTx`/`GaslessSwapTx` pair and extract value from the victim, exactly as in the referenced report's `multiHopSell`/`multiHopBuy` frontrunning issue.

### Finding Description
`checkBalanceForSwap` is the only place the node validates a `GaslessSwapTx` before promotion/bundling: [1](#0-0) 

It only guarantees `minAmountOut >= amountRepay` (so the swap output covers the gas debt) and, optionally, that the declared `amountIn` matches the router's `GetAmountIn(minAmountOut)` computed against the **current** on-chain reserves at admission time: [2](#0-1) 

Neither check bounds the *maximum* acceptable slippage between the price observed when the client computed `minAmountOut` and the price at actual on-chain execution. The gasless bundle (`LendTxGenerator + ApproveTx + SwapTx`) is only guaranteed atomicity/ordering with respect to itself: [3](#0-2) 

There is no mechanism preventing an unrelated, unprivileged sender from placing a large trade on the very same underlying AMM pool (the same Uniswap-style router/pair the `GaslessSwapRouter` uses, as exercised in `TestGasless`) immediately before the gasless bundle in the same block, and reversing it immediately after (classic sandwich). Because `GaslessSwapTx`, like any pending transaction, is visible in the mempool before block assembly, and the router's price check inside `checkBalanceForSwap` is only evaluated against current-at-admission reserves (not at inclusion time), a searcher can:

1. Observe a pending `GaslessSwapTx` with `minAmountOut` set only marginally above `amountRepay` (i.e., a "high-slippage-tolerance" swap, mirroring the audited report's exact bug class).
2. Front-run with a large buy on the pool token to move the price unfavorably for the victim.
3. Let the victim's `swapForGas` execute and receive close to the `minAmountOut` floor instead of the fair-market amount.
4. Back-run to realize the extracted value.

This is architecturally identical to the reported `multiHopSell`/`multiHopBuy` issue: a fixed, attacker-visible `amountOutMin`/`minAmountOut` with no additional protections is trivially sandwichable by any unprivileged transaction sender.

### Impact Explanation
A successful sandwich directly steals value from gasless-transaction users: the difference between the fair swap output and the `minAmountOut` floor is captured by the attacker instead of the user, which is a concrete unauthorized value movement/gasless-settlement theft as defined in scope. Because gasless transactions are explicitly designed to onboard fee-less/novice users (who are the least likely to set tight custom slippage), this issue is broadly reachable and impactful.

### Likelihood Explanation
Any public-RPC caller / unprivileged EOA can observe pending `GaslessSwapTx` transactions (they are ordinary transactions targeting a known, whitelisted `GaslessSwapRouter` and following a fixed ABI, as decoded in `decodeSwapTx`), and construct front-run/back-run trades against the same underlying liquidity pool without needing any special privilege, node access, or validator/proposer role. The only precondition is that a victim submits a swap with a slippage tolerance (`minAmountOut - amountRepay` margin) wide enough to be profitably sandwiched, which the module does nothing to prevent or bound.

### Recommendation
- Enforce a maximum allowed slippage/price-impact bound (e.g., relative to a TWAP or a router-provided max-slippage parameter) in `checkBalanceForSwap`, rather than only checking `minAmountOut >= amountRepay`.
- Re-validate the swap's expected output against current reserves immediately before block inclusion (not just at admission), or reject/re-queue bundles whose price context changed significantly.
- Consider protecting gasless bundles from being sandwiched by non-bundle transactions touching the same pool within the same block (e.g., via bundle exclusivity/ordering guarantees enforced by the builder).

### Proof of Concept
1. A user submits `GaslessApproveTx` + `GaslessSwapTx` (`swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)`) with `minAmountOut` set only slightly above `amountRepay` (default/naive slippage setting), as in the test harness: `minAmountOut = amountRepay + margin` where `margin = expectedOutput/100`. [4](#0-3) 
2. An attacker observing this pending transaction submits a large swap on the same AMM pool immediately before it (front-run), shifting the exchange rate against the victim.
3. The proposer's bundle executes `LendTxGenerator → ApproveTx → SwapTx`; because the router's on-chain check only enforces `amountOut >= minAmountOut`, the victim's swap succeeds but yields close to the (already thin) `minAmountOut` floor instead of the pre-attack expected amount.
4. The attacker back-runs to close out their position, realizing the captured slippage as profit at the gasless user's expense.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L102-120)
```go
// tx.minAmountOut >= tx.amountRepay
// tx.amountIn >= gsr.getAmountIn(minAmountOut)
// tx.token.approval(sender, router) >= tx.amountIn
// tx.token.balanceOf(sender) >= tx.amountIn
// tx.deadline >= currentTimestamp
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
```

**File:** kaiax/gasless/impl/tx_pool.go (L128-142)
```go
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

**File:** tests/gasless_test.go (L151-163)
```go
	var (
		gasPriceBN         = new(big.Int).Mul(big.NewInt(50), bigGkei)
		R1                 = new(big.Int).Mul(big.NewInt(21000), gasPriceBN)
		R2                 = new(big.Int).Mul(big.NewInt(100000), gasPriceBN)
		R3                 = new(big.Int).Mul(big.NewInt(500000), gasPriceBN)
		ammontRepay        = new(big.Int).Add(R1, new(big.Int).Add(R2, R3))
		amountRepaySwap    = new(big.Int).Add(R1, R3)
		transferToken      = new(big.Int).Mul(big.NewInt(100), bigKaia)
		swapExpectedOutput = amountsOut[1]
		margin             = new(big.Int).Div(swapExpectedOutput, big.NewInt(100))
		minAmountOut       = new(big.Int).Add(ammontRepay, margin)
		deadline           = new(big.Int).Add(chain.CurrentBlock().Time(), big.NewInt(300))
	)
```

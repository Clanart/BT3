### Title
Gasless swap price validated at mempool admission but not re-verified against pool state at bundle execution, allowing proposer loss via DEX price manipulation - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The Belt Finance incident was a flash-loan attack where the attacker manipulated a pool's internal exchange-rate/share-price accounting between the moment a value was validated and the moment funds were actually moved, extracting funds by exploiting a stale price check. Kaia's gasless (KIP-247) module has an analogous check-then-use gap: the txpool validates a `GaslessSwapTx`'s declared `minAmountOut`/`amountIn` against the *current* DEX reserves at admission time, but the proposer has already committed to prepending a `LendTx` that funds the user's gas before the swap executes, with repayment (`amountRepay`) depending on the swap's actual output at execution time.

### Finding Description
`checkBalanceForSwap` in [1](#0-0)  validates a submitted `GaslessSwapTx` by calling `routerContract.GetAmountIn(nil, token, minAmountOut)` against a read-only contract call on the *current* chain state, and only requires `tx.AmountIn >= requiredAmountIn` at that snapshot. This is a point-in-time admission check, not an execution-time guarantee.

The gasless flow works by having the proposer front the user's gas via a `LendTx`, bundled together with the user's `GaslessApproveTx`/`GaslessSwapTx` as documented in [2](#0-1) , and executed via `ExtractTxBundles` in [3](#0-2) . The proposer is repaid from the swap's proceeds (`amountRepay`), and the module only enforces `minAmountOut >= amountRepay` as a *declared* invariant checked against a stale snapshot of the underlying AMM (Uniswap-style) pool reserves fetched via `GetAmountIn` in [4](#0-3) .

Because this AMM pool's reserves are only checked once, at tx admission (`PreAddTx`/`GetCheckBalance`), and the actual swap executes later (potentially several blocks later while the tx sits in the pool, or in a different price environment than when validated), an attacker who is simply an unprivileged, ordinary transaction sender can move the underlying DEX pool's price between validation and inclusion — no privileged/validator role is required, since anyone can submit ordinary swap transactions against the same public AMM pool the gasless router uses (as demonstrated by the same test harness deploying UniswapV2Factory/Router alongside `GaslessSwapRouter` in [5](#0-4) ). This can cause the actual swap output at execution time to differ materially from the value used to satisfy the `minAmountOut >= amountRepay` check at admission time.

Because this repo does not include the Solidity source of `GaslessSwapRouter` (only the compiled/generated Go bindings in [6](#0-5) ), I could not directly confirm whether the on-chain `swapForGas` function itself independently enforces a live slippage/minAmountOut check at execution time that would fully neutralize this admission-time-vs-execution-time gap. This is a genuine gap in verification — the vulnerability's exploitability hinges on this unverified on-chain behavior.

### Impact Explanation
If the on-chain contract does not itself re-verify the actual swap output against a freshly-computed minimum at execution time (relying instead on the mempool/admission check), an attacker can manipulate the AMM pool price (via ordinary public swaps against the same pool) between admission and inclusion so that the gasless swap executes at a worse rate than validated. This could result in the proposer not being fully repaid the gas it fronted via the `LendTx` (direct fee/value loss to the proposer), or in the extreme, in repeated exploitation, meaningful drain of proposer-fronted funds — a fee-delegation abuse pattern analogous to the value-extraction mechanism in the Belt Finance incident (manipulate a price/exchange-rate variable to siphon value through a legitimate-looking transaction flow).

### Likelihood Explanation
The precondition (moving an AMM pool's price with ordinary transactions) is trivially achievable by any unprivileged sender with sufficient capital or via a same-block sandwich, especially since Kaia bundles the `LendTx` + `GaslessApproveTx`/`GaslessSwapTx` together at block-building time — meaning the price the mempool checked at submission time can be stale by the time the bundle is finally included, especially under queueing delays (`QueueTimeout`, `PendingTimeout` in [7](#0-6) ) of up to 10+ seconds, multiple blocks in a busy network. However, likelihood is capped because I could not confirm (due to missing Solidity source) whether the on-chain `swapForGas` already independently reverts on insufficient output — if it does, this issue reduces to a denial-of-service/wasted-gas issue rather than value theft.

### Recommendation
- Confirm/enforce that the `GaslessSwapRouter.swapForGas` contract itself performs a live, on-chain slippage check (`amountOut >= minAmountOut`) at execution time, independent of the mempool's admission-time check, and reverts the whole bundle (including the `LendTx`) atomically if unmet.
- Re-validate `checkBalanceForSwap`'s price/amount conditions immediately before block inclusion (`IsReady`) using the state used to build the current block, not merely at initial `PreAddTx` admission, to shrink the staleness window.
- Consider requiring the proposer's `LendTx` and the user's swap to be executed with slippage protection that guarantees the proposer is only ever exposed to funding gas they can recoup, e.g., verifying repayment atomically within the same bundle/transaction rather than relying on optimistic mempool-time estimates.

### Proof of Concept
Conceptual sequence (bounded by inability to inspect the on-chain contract's execution-time checks):
1. Attacker (or colluding party) submits `ApproveTx` + `SwapTx` (`GaslessSwapTx`) with `minAmountOut` computed from the pool's current reserves via `GetAmountIn`, satisfying `checkBalanceForSwap` in [8](#0-7) .
2. Before the bundle (`LendTx`, `ApproveTx`, `SwapTx`) is included by the proposer (which can be delayed due to `QueueTimeout`/`PendingTimeout` in [7](#0-6) , or simply due to ordinary block gaps), the attacker (or an accomplice) submits ordinary public swap transactions against the same underlying Uniswap-style pool used by `GaslessSwapRouter` (same pool deployed and referenced as in [5](#0-4) ), shifting reserves so that the actual token-to-KAIA rate drops.
3. When the bundle is finally executed, `swapForGas` converts the same `amountIn` of token into a lower amount of KAIA than was assumed at admission time.
4. If the contract does not independently re-check `minAmountOut` on-chain (unverifiable here due to missing Solidity source), the proposer's `amountRepay` claim may not be fully honored, and the proposer absorbs the shortfall from having pre-funded the user's gas via `LendTx`.

This report necessarily flags an **unverified assumption** — the on-chain `swapForGas` slippage-enforcement logic is not present in this repository's indexed content (only compiled bytecode/bindings). A Devin session with full repository/filesystem access should be used to pull the actual Solidity source (if available in a separate `contracts` submodule, npm package, or via decompilation) to confirm or refute the presence of execution-time minAmountOut enforcement before treating this as a confirmed exploitable finding.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L32-36)
```go
var (
	QueueTimeout   = 10 * time.Second // bundle tx is removed from txpool.queue after QueueTimeout
	PendingTimeout = 10 * time.Second // bundle tx is removed from txpool.pending after PendingTimeout
	KnownTxTimeout = 30 * time.Second // bundle tx is removed from knownTxs after KnownTxTimeout
)
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

**File:** kaiax/gasless/README.md (L29-36)
```markdown
### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
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

**File:** tests/gasless_test.go (L100-105)
```go
	/* ------------- Deploy contracts ------------- */
	testTokenAddr, testTokenContract := deployTestToken(t, chain, transactor, owner, owner.Addr)
	wkaiaAddr, wkaiaContract := deployWKAIA(t, chain, transactor, owner)
	factoryAddr, factoryContract := deployUniswapV2Factory(t, chain, transactor, owner, owner.Addr)
	routerAddr, routerContract := deployUniswapV2Router02(t, chain, transactor, owner, factoryAddr, wkaiaAddr)
	gsrAddr, gsrContract := deployGaslessSwapRouter(t, chain, transactor, owner, wkaiaAddr)
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L554-559)
```go
// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) SwapForGas(opts *bind.TransactOpts, token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "swapForGas", token, amountIn, minAmountOut, amountRepay, deadline)
}
```

### Title
Gasless module trusts a manipulable AMM spot price (`GetAmountIn`) as an authoritative exchange rate for gas repayment sizing - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The external report's bug class is "trusting an external price/peg as fixed when it can be manipulated/deviate," causing wrong accounting that a transaction sender can exploit. In Kaia's gasless module, the `swapForGas` amount-in requirement is derived from a single, un-buffered spot-price call to the `GaslessSwapRouter`'s `GetAmountIn`, which is itself backed by a Uniswap-V2-style AMM pool reserve ratio. This spot price is trivially manipulable within the same block (or between admission-time check and inclusion) and is treated as authoritative for validating that the gasless sender is providing enough collateral token to repay the CN operator's fee-lending.

### Finding Description
The gasless flow lets an unprivileged user submit an ERC20 `approve` + `swapForGas` transaction pair, without holding native KAIA, to pay gas. The Kaia node fronts (lends) the KAIA gas fee via a generated `LendTx`, and expects the swap to repay that exact amount from `AmountRepay` using the ERC20 token, swapped by the AMM pool inside `GaslessSwapRouter`.

The pool-admission check `checkBalanceForSwap` in [1](#0-0)  validates the user-declared `AmountIn` against `routerContract.GetAmountIn(nil, token, minAmountOut)` — a single read of the AMM's current spot exchange rate, with no minimum liquidity check, no staleness/deviation check, and no reference to any external oracle or TWAP. This is architecturally analogous to the reported issue: the protocol assumes a value (USDC/USD peg in the original report; token/KAIA AMM spot rate here) is stable and can be safely used for value accounting, when in fact it is a live, attacker-influenceable market price.

Because `GetAmountIn` reflects only the current on-chain reserves of the router's underlying pool at call time, an attacker who also controls (or front/back-runs) the pool's liquidity can:
1. Manipulate the pool reserves (e.g., via a large swap in the same or an adjacent transaction/bundle) to temporarily depress the effective price of the token in terms of KAIA.
2. Get `GetAmountIn` to return an artificially low `requiredAmountIn` for the desired `minAmountOut`.
3. Submit `swapForGas` with `AmountIn` just above this manipulated low value, satisfying `checkBalanceForSwap` at admission/execution time, and get the router to actually execute the swap at the same manipulated (low) rate — meaning the sender pays much less real token value than the honest market rate to obtain the KAIA needed to repay the CN's lent gas.
4. Actual value transferred to the CN operator (fee lender) as repayment relies on the AMM being fair at swap-execution time (`GetAmountIn`/on-chain AMM execution price at inclusion), so if the whole manipulation + swapForGas executes atomically within the same block/bundle, the "market rate" backing the entire lend/repay accounting is self-manipulated by the same actor benefiting from it.

This check is exercised purely from the transaction pool admission path reachable by any unprivileged sender submitting a normal transaction pair — no special privilege is required. [2](#0-1) 

### Impact Explanation
If the sender can manipulate the price used to size `AmountIn`/`minAmountOut` for the gasless swap, they can obtain the KAIA lent by the CN (used to pay `LendTx`/actual gas of `ApproveTx`+`SwapTx`) while contributing less real economic value in ERC20 tokens than intended, directly at the expense of whichever party bears the swap execution risk (the CN operator who fronts gas, or liquidity providers in the pool who absorb the manipulated trade). This is a concrete fee-delegation/gasless settlement abuse: value moves out of the lending/repay mechanism at an unfair rate due to reliance on an unguarded spot price, matching the "gasless or auction settlement theft" category explicitly named as in-scope.

### Likelihood Explanation
Likelihood is Medium: it requires the attacker to also be able to move the specific AMM pool's price (e.g., via low liquidity pools, which are plausible for newly whitelisted gasless tokens), and to package their price-manipulating swap and their `swapForGas` transaction so both execute in the same block before arbitrage/backend fully corrects the price. Given `checkBalanceForSwap` is invoked at both tx-pool admission and can be re-validated close to inclusion, and there is no minimum-liquidity or price-impact-bound enforcement visible in the reachable code (`ShouldCheckSwapAmount` only compares `AmountIn` to the single spot-derived `requiredAmountIn`), an attacker fully controlling the target token/pool (which they can create/select via whichever token gets whitelisted, or via low-liquidity real pools) can reliably trigger this.

### Recommendation
- Do not rely solely on the AMM's live spot price from `GetAmountIn` to size the swap requirement. Enforce a maximum allowed price deviation from a TWAP, external oracle, or a bounded slippage/price-impact check before admitting or promoting the swap tx.
- Enforce a minimum pool liquidity/reserve threshold for tokens usable in gasless swaps so that price manipulation is economically costly.
- Consider re-validating `checkBalanceForSwap` at actual execution/inclusion time against a TWAP rather than allowing a single instantaneous reserve-ratio read to gate both admission and settlement.
- Ensure `ShouldCheckSwapAmount` cannot be disabled in production configurations, since disabling it removes the last exchange-rate check entirely.

### Proof of Concept
Conceptual reproduction path (cannot be fully executed without a live testnet, but traceable through code):
1. Attacker deploys or selects a whitelisted gasless token whose AMM pool (behind `GaslessSwapRouter`) has shallow liquidity.
2. Attacker (or a colluding bundle) executes a large swap against the pool to skew reserves and depress the token's implied KAIA price.
3. In the same block, attacker submits `approve` + `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` where `amountIn` is computed to just satisfy `routerContract.GetAmountIn(nil, token, minAmountOut)` at the manipulated price, per the check in [1](#0-0) .
4. `checkBalanceForSwap` passes because it only compares against the currently-manipulated spot-derived `requiredAmountIn`; the CN lends gas via `LendTx` per `lendAmount`/`repayAmount` accounting in [3](#0-2) , and the swap executes at the attacker-favorable rate, completing the gasless flow with tokens worth less than the KAIA fronted by the operator.

Note: I was not able to fully trace the exact runtime ordering guarantees (whether the manipulating swap and `swapForGas` are guaranteed to land in the same block/bundle, or whether any bundle-atomicity mechanism in the block-builder prevents interleaving) within the available indexed code, so the "same-block atomicity" assumption underlying step 2–3 above is asserted based on general AMM MEV mechanics rather than confirmed Kaia-specific bundling guarantees for this exact path.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L107-141)
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
```

**File:** kaiax/gasless/impl/getter.go (L346-367)
```go
func lendAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	r := new(big.Int)

	// R2 = ApproveTx.Fee() if exists
	if approveTxOrNil != nil {
		r.Add(r, approveTxOrNil.Fee())
	}

	// R3 = SwapTx.Fee()
	r.Add(r, swapTx.Fee())

	// LendAmount = R2 + R3
	return r
}

func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))

	// RepayAmount = R1 + R2 + R3
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```

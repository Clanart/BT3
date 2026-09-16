### Title
Sandwich-manipulable spot AMM price in `GaslessSwapRouter.getAmountIn`/`swapForGas` allows theft from gasless swap users - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The KIP-247 gasless-transaction flow relies on `GaslessSwapRouter.getAmountIn(token, amountOut)` — a view function that derives its exchange rate from the *spot reserves* of the underlying UniswapV2-style DEX pair — both to admit a `GaslessSwapTx` into the pool and to execute the actual token→KAIA conversion in `swapForGas`. Exactly like the `SponsorVault.reimburseLiquidityFees` bug (spot-price AMM query abused via sandwich attack), an attacker who can submit ordinary swap transactions against the same DEX pool in the same block can manipulate the spot price the gasless flow depends on, extracting value at the expense of the gasless user/proposer.

### Finding Description
The gasless tx-pool admission logic computes the required input amount directly from the router's live AMM price: [1](#0-0) 

`GetAmountIn` on `GaslessSwapRouter` is a plain view call into the DEX (UniswapV2 reserves-based pricing, same style as `getAmountsIn`/`getAmountOut` on `UniswapV2Router02`): [2](#0-1) 

The actual value transfer happens on-chain in `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)`, which performs the token→KAIA swap through the DEX at whatever the pool's spot price is at execution time, then repays the proposer's lent gas and forwards the remainder (`FinalUserAmount`) to the user, taking a commission: [3](#0-2) [4](#0-3) 

The only price protection is `minAmountOut`, which is a user-supplied lower bound checked against `amountRepay` and the pool's *current* spot price at tx-pool-admission time: [5](#0-4) 

Nothing here compares the spot price against an oracle/TWAP or otherwise bounds legitimate slippage beyond the attacker-influenced `minAmountOut` the *user* chose when the price looked fair off-chain. Because the gasless bundle (`[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]`) is only guaranteed to execute atomically as a unit — it does not reserve exclusive access to the DEX pool for the whole block — an attacker who is an ordinary, unprivileged transaction sender on the same DEX pair can:

1. Front-run the gasless bundle with a large swap against the pool used by `GaslessSwapRouter`, moving the token price against the gasless user's swap direction (token→KAIA).
2. Let the bundle's `swapForGas` execute at this manipulated price, still satisfying `minAmountOut` (the same slippage-tolerance ceiling attackers famously ride down to, per the classic sandwich pattern from the referenced SponsorVault bug) but yielding materially less KAIA output than the fair spot price would produce.
3. Back-run with the reverse trade, realizing a profit extracted from the value that should have gone to the gasless user's `FinalUserAmount`, the proposer's `amountRepay`, or the protocol's commission.

This is architecturally identical to the reported vulnerability class: a privileged-looking, protocol-critical financial operation (swapping a fee/gas-value asset) is priced by directly querying an AMM's manipulable spot reserves rather than a manipulation-resistant oracle/TWAP, with only a user-set `minAmountOut`/slippage bound as protection — precisely the primitive a sandwich attacker exploits.

### Impact Explanation
Sandwiching the gasless swap allows an attacker to skim value from every `GaslessSwapTx` executed via the affected DEX pair, degrading either the user's net repayment amount, the proposer's gas repayment guarantee, or router commission revenue. Since `swapForGas` is core to the gasless-tx settlement path (a feature explicitly reachable by any gasless user and open to any unprivileged trader on the underlying DEX pool), this constitutes concrete unauthorized value extraction / fee-delegation abuse against a listed in-scope module (gasless), matching the Medium/High severity bar for this bug class.

### Likelihood Explanation
The precondition — being able to submit ordinary swap transactions against the whitelisted token's DEX pair in the same block as a `GaslessSwapTx` — requires no special privilege; any transaction sender or auction bidder capable of getting transactions ordered around the gasless bundle in the same block can execute this. Given `checkBalanceForSwap` only re-derives `getAmountIn` against the *current* mempool-time spot price without any oracle cross-check, and the on-chain `swapForGas` execution similarly trusts spot reserves, exploitation is straightforward once liquidity in the relevant pool is thin enough to move economically.

### Recommendation
`GaslessSwapRouter.getAmountIn` (and the swap performed in `swapForGas`) should not rely purely on the DEX's instantaneous spot reserves. Cross-check the spot price against a manipulation-resistant reference (e.g., a TWAP over several blocks, or an external oracle) and revert or clamp the swap when the deviation exceeds a configured threshold, mirroring the fix pattern ("Connext: Solved in PR 1595") applied to the original SponsorVault issue. Additionally, the gasless tx-pool/bundle logic in `kaiax/gasless/impl/tx_pool.go` could bound acceptable `minAmountOut` slippage tolerance to a small percentage of the oracle-derived fair price rather than trusting a purely user-supplied value validated only against the same manipulable spot query.

### Proof of Concept
Conceptually (cannot be executed here, but derivable from the referenced code):
1. Attacker observes a pending `GaslessApproveTx` + `GaslessSwapTx` pair (public mempool/tx pool) for token `T` against pool `T/WKAIA`.
2. Attacker submits a large `swapExactTokensForTokens`/`swapExactETHForTokens`-style trade on the same pool immediately ahead of the gasless bundle in block-building order, shifting the reserves so `T`'s value in KAIA drops.
3. The gasless bundle's `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` executes at the depressed spot price; because `checkBalanceForSwap`'s `GetAmountIn` check (`kaiax/gasless/impl/tx_pool.go:128-141`) and the actual on-chain swap both use the same manipulable spot reserves, the trade still satisfies `minAmountOut >= amountRepay` but yields far less than the "fair" output.
4. Attacker submits a reverse trade to restore the price and pocket the difference, which is effectively taken from the value the gasless user (and/or proposer commission) would otherwise have received.

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

**File:** kaiax/gasless/impl/tx_pool.go (L128-141)
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
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L301-316)
```go
// GetAmountIn is a free data retrieval call binding the contract method 0x632db21c.
//
// Solidity: function getAmountIn(address token, uint256 amountOut) view returns(uint256 amountIn)
func (_GaslessSwapRouter *GaslessSwapRouterCaller) GetAmountIn(opts *bind.CallOpts, token common.Address, amountOut *big.Int) (*big.Int, error) {
	var out []interface{}
	err := _GaslessSwapRouter.contract.Call(opts, &out, "getAmountIn", token, amountOut)

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}
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

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L1127-1135)
```go
// GaslessSwapRouterSwappedForGas represents a SwappedForGas event raised by the GaslessSwapRouter contract.
type GaslessSwapRouterSwappedForGas struct {
	Proposer        common.Address
	AmountRepaid    *big.Int
	User            common.Address
	FinalUserAmount *big.Int
	Commission      *big.Int
	Raw             types.Log // Blockchain specific contextual infos
}
```

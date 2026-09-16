Based on my research, I found a plausible analog: the KIP-247 gasless-transaction flow relies on a live, spot-price AMM quote (`getAmountIn` against a Uniswap-V2-style pool's `getReserves`) both to admit a gasless swap into the pool and (via the deployed `GaslessSwapRouter`) to execute the actual swap — the same bug class (AMM spot-price manipulation, "flash-loan"-style) that enabled the Pancake Hunny incident. I was unable to fully confirm bundle-execution atomicity in this final pass, so I flag that explicitly below.

### Title
Gasless swap admission and settlement rely on manipulable AMM spot price, enabling proposer fee-lending loss - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
`GaslessModule.checkBalanceForSwap` validates a user's gasless-swap transaction by querying the whitelisted `GaslessSwapRouter`'s `GetAmountIn(token, minAmountOut)`, which is itself derived from a live Uniswap-V2-style pool's reserves (spot price) [1](#0-0) . This is analogous to the Pancake Hunny incident, where an attacker used a flash loan to distort an AMM pool's spot price and defeat a protocol's price-dependent logic.

### Finding Description
The gasless flow works as: `LendTxGenerator` (proposer-funded) sends the user KAIA to pay gas, then `GaslessApproveTx`/`GaslessSwapTx` execute, with the user's `swapForGas` call expected to repay `amountRepay` to the proposer from the swapped token output [2](#0-1) . The bundle is built as `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` [3](#0-2) .

The only sanity check performed before promoting/admitting a swap transaction is that `amountIn >= gsr.getAmountIn(token, minAmountOut)`, computed against the *current* DEX reserves at admission time [4](#0-3) . The actual `swapForGas` execution enforces only `minAmountOut >= amountRepay` and performs the real swap through the underlying DEX router/pair, whose price is the same manipulable reserve-based spot price (`IUniswapV2Pair.GetReserves` / `getAmountIn`/`getAmountOut`) [5](#0-4) [6](#0-5) .

Because the swap's viability (whether it reverts or produces sufficient output to cover `amountRepay`) depends entirely on the pool's spot reserves at the moment `swapForGas` actually executes — not at admission time — an attacker who is simultaneously an unprivileged gasless user can submit ordinary transactions (self-sequenced by nonce/gas price, requiring no special privilege) that shift the target token's pool reserves between the `LendTxGenerator` transfer and the `GaslessSwapTx` execution within the same block. This can force the swap to revert after the proposer's KAIA lend has already been unconditionally transferred to the user, since the lend transaction and the swap transaction are distinct transactions in the block rather than a single atomic call.

### Impact Explanation
If the lend transfer and swap settlement are not atomically reverted together, an attacker can repeatedly trigger proposer-funded lends that are never repaid by engineering pool-price shifts that cause `swapForGas` to revert on `amountRepay` after the free gas has already been disbursed — a concrete fee-delegation/gasless-settlement value loss to the block proposer, matching the accepted impact classes (fee-delegation abuse / gasless settlement theft). This is Medium severity: it does not compromise consensus or allow arbitrary token theft, but it allows economic griefing/drain of proposer-funded gas subsidies through price manipulation of a live AMM oracle, mirroring the underlying bug class of the Pancake Hunny incident (spot-price manipulation defeating protocol economic assumptions).

### Likelihood Explanation
Reachable by any unprivileged, whitelisted gasless-token holder/sender using only ordinary public transactions (no special node or validator privilege), which matches the reachable actor classes (gasless user, unprivileged transaction sender). The likelihood depends on: (a) the whitelisted token's DEX pool having shallow enough liquidity to move materially within a block using capital available to a normal user, and (b) the block-builder/bundle logic not re-simulating or reverting the entire bundle atomically if the swap fails after the lend has been included. I was not able to fully confirm point (b) — I could not inspect the exact implementation of `GetLendTxGenerator` or the `work/builder` bundle-execution semantics (whether a bundle failing partway is rolled back as a unit) within the remaining investigation budget, so the ultimate severity depends on that unverified detail.

### Recommendation
- Re-validate `checkBalanceForSwap`'s required-`amountIn` computation immediately before/atomically with `swapForGas` execution (e.g., using the same block context) rather than relying on admission-time-only checks.
- Ensure the `LendTxGenerator` transfer and the `GaslessSwapTx` settlement are atomically bundled such that a reverted swap also reverts/reclaims the lend (or make the lend conditional on swap success within the same call frame).
- Consider requiring the price source used for `getAmountIn`/`getAmountOut` to be resistant to single-block manipulation (e.g., TWAP or minimum-liquidity/maximum-price-impact guards) rather than raw instantaneous reserves.

### Proof of Concept
Conceptual (could not be fully executed/verified in this pass since `GetLendTxGenerator`'s body and bundle-atomicity guarantees in `work/builder` were not directly inspected):
1. Attacker holds a whitelisted gasless token with a shallow DEX pool.
2. Attacker submits, within the same block/nonce sequence: (a) a large swap against the token's pool to shift reserves, (b) a `GaslessApproveTx`+`GaslessSwapTx` pair whose `amountIn`/`minAmountOut` were computed/admitted against the pre-manipulation price, (c) a reversing swap to restore the pool.
3. The `LendTxGenerator` transaction (step inserted by the builder before the approve/swap) transfers KAIA gas funding to the attacker unconditionally [7](#0-6) .
4. Due to the manipulated reserves, `swapForGas` reverts (output insufficient to satisfy `amountRepay`/`minAmountOut`), so the proposer is never repaid, while the attacker keeps the lent gas value and pays only pool-manipulation costs. [4](#0-3)

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L102-141)
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

**File:** kaiax/gasless/README.md (L7-36)
```markdown
Gasless transaction (GaslessTx) consists of two types: gasless approve transaction (GaslessApproveTX), and gasless swap transaction (GaslessSwapTx).

Note that gasless transaction does not mean the gas prices of gasless transactions are zero, but it means that proposer will lend the user with gas fee and user will pay back during gasless swap.

The gas fee of gasless transaction's is funded by block proposer (i.e., lend transaction generated by `GetLendTxGenerator`), and user repays the lent amount during gasless swap.

### Transaction pool rules

#### Ready

This module is responsible for promoting gasless transactions.
Sender's nonce of GaslessSwapTx is checked to distinguish if GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender) + 1`, GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender)`, GaslessApproveTx is not expected.

If GaslessApproveTx is expected, GaslessApproveTx and GaslessSwapTx can be promoted when they are both ready for execution.
Otherwise, GaslessSwapTx can be promoted when it is ready for execution.

See ready condition [KIP-247](https://kips.kaia.io/KIPs/kip-247) and the implementation `IsExecutable(approveTxOrNil, swapTx *types.Transaction) bool`.

#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).

### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
```

**File:** kaiax/gasless/impl/builder.go (L38-51)
```go
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
```

**File:** contracts/bindings/uniswap/factory/UniswapV2Factory.go (L2665-2691)
```go
// GetReserves is a free data retrieval call binding the contract method 0x0902f1ac.
//
// Solidity: function getReserves() view returns(uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast)
func (_IUniswapV2Pair *IUniswapV2PairCaller) GetReserves(opts *bind.CallOpts) (struct {
	Reserve0           *big.Int
	Reserve1           *big.Int
	BlockTimestampLast uint32
}, error,
) {
	var out []interface{}
	err := _IUniswapV2Pair.contract.Call(opts, &out, "getReserves")

	outstruct := new(struct {
		Reserve0           *big.Int
		Reserve1           *big.Int
		BlockTimestampLast uint32
	})
	if err != nil {
		return *outstruct, err
	}

	outstruct.Reserve0 = *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)
	outstruct.Reserve1 = *abi.ConvertType(out[1], new(*big.Int)).(**big.Int)
	outstruct.BlockTimestampLast = *abi.ConvertType(out[2], new(uint32)).(*uint32)

	return *outstruct, err
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

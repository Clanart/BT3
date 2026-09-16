## Analysis Summary

This bundle confirms the analog: `LendTx` (from the CN proposer) is placed **before** `ApproveTx`/`SwapTx` in the bundle, and the entire bundle is treated as an ordered sequence built by `ExtractTxBundles` [1](#0-0) . The `LendAmount`/`RepayAmount` are fixed, gas-price-derived quantities independent of the DEX swap outcome [2](#0-1) , while the only check that the user's token deposit is "enough" is `checkBalanceForSwap`, which quotes `GetAmountIn` from the swap router's live on-chain DEX price with no oracle or price-band cross-check [3](#0-2) .

### Title
Gasless SwapForGas admission check trusts a manipulable spot DEX price with no oracle/price-band cross-check, enabling sandwich extraction against CN-fronted KAIA and pool liquidity - (File: `kaiax/gasless/impl/tx_pool.go`)

### Summary
The Gasless module lets an unprivileged, gasless user submit an `ApproveTx`+`SwapTx` pair that a CN proposer bundles with a self-funded `LendTx` fronting KAIA gas, to be repaid from the token→KAIA swap proceeds. The admission-time solvency check (`checkBalanceForSwap`) validates `amountIn` against `GetAmountIn`, a quote read directly from the swap router's current DEX pool state, with no reference to any independent price oracle or price-deviation bound. This mirrors the SpotHedgeBaseMaker issue: a value-moving mechanism is gated purely by an AMM's instantaneous, attacker-manipulable price.

### Finding Description
`checkBalanceForSwap` computes the minimum required `amountIn` for the user's declared `minAmountOut` by calling `routerContract.GetAmountIn(nil, token, minAmountOut)` against the current chain state [3](#0-2) . `GetAmountIn`, per the router bindings, is a DEX-router style spot-price quote (analogous to `getAmountsOut`/`getAmountIn` on a constant-product pool) [4](#0-3) . There is no comparison against an oracle price or bounded deviation check anywhere in this validation path, unlike the KIP-71 base-fee mechanism which enforces hard `LowerBoundBaseFee`/`UpperBoundBaseFee` bounds derived from protocol-controlled parameters [5](#0-4) .

The economically meaningful amounts in this flow are fixed and price-independent: `lendAmount`/`repayAmount` are computed purely from transaction gas fees, not from DEX-quoted values [2](#0-1) . The bundle building logic places the CN's `LendTx` (a genuine value transfer of the proposer's own KAIA to the user) ahead of the `ApproveTx`/`SwapTx` sequence [6](#0-5) . Because the DEX price used both for the mempool admission gate and for the actual on-chain swap execution is the same manipulable pool state, an attacker who also controls a preceding pool-skewing transaction in the same block can:
1. Skew the token/KAIA DEX pool price in one direction (temporarily inflating the token's KAIA-denominated value).
2. Submit `ApproveTx`/`SwapTx` with an `amountIn` that easily satisfies `GetAmountIn`/`checkBalanceForSwap` under the skewed price, while the token amount is worth far less at the true market price.
3. Let `swapForGas` execute against the still-skewed pool, producing at least `minAmountOut` (i.e., enough KAIA to satisfy `amountRepay` to the CN and any `FinalUserAmount` refund to the attacker) — value effectively extracted from the DEX pool's liquidity providers, and/or a device to deterministically manufacture enough output to satisfy the CN's repayment while under-delivering real economic value.
4. Reverse the pool skew in the same block, pocketing the difference risk-free — an attack pattern identical in structure to the Sherlock M-8 SpotHedgeBaseMaker sandwich, where trades against an AMM inside a single atomic sequence, unconstrained by any oracle price band, allow the attacker to extract value up to the limits of the (nonexistent, here) price band.

### Impact Explanation
This allows an unprivileged sender (the gasless-tx submitter, who can also front-run/back-run their own bundle via ordinary transactions in the same block) to manipulate the DEX price used by both the CN's admission check and the swap execution, extracting value from the pool and/or manufacturing an artificially "solvent" swap that satisfies the fixed `amountRepay` obligation to the CN with under-market-value token collateral. This is fee-delegation/gasless-settlement abuse and potential value extraction against CN-fronted KAIA and DEX liquidity, matching the "gasless or auction settlement theft" and "fee-delegation abuse" categories.

### Likelihood Explanation
Reachable by any ordinary transaction sender with no special privilege — they only need to submit a standard `ApproveTx`/`SwapTx` pair alongside their own pool-manipulating transaction(s) in the same block, exactly as in the original report's flash-loan/sandwich pattern. The main precondition (as in the original Sherlock finding) is sufficient capital and low enough DEX liquidity/fees to make the price swing profitable — a scenario the original judges accepted as Medium severity despite requiring specific market conditions.

### Recommendation
- Cross-check the DEX-quoted `amountIn`/`minAmountOut` against an independent price oracle (or a governance-configured allowed deviation band), rejecting swaps whose implied exchange rate deviates too far from a trusted reference price, analogous to `ClearingHouse`'s price-band check in the original report.
- Re-validate `GetAmountIn`/pool price at actual inclusion time (not just admission time), and/or require the swap execution to occur in the exact same block/state snapshot used for the admission check so a manipulated quote cannot diverge from execution.
- Consider TWAP-based quoting instead of instantaneous spot price for `GetAmountIn`/`GetAmountsOut` in the swap router.

### Proof of Concept
Not independently reproducible from the indexed code alone: constructing a concrete PoC requires knowledge of the actual `GaslessSwapRouter`/DEX pool implementation (liquidity depth, fee tier, `GetAmountIn` formula) which is only available as compiled bytecode/ABI in the indexed sources [7](#0-6) ; the Solidity source for the router/DEX pair was not found in the index. Start a Devin session with full repository access to build a concrete numeric PoC (mirroring the original Sherlock `testSandwich` scenario) using the real `GaslessSwapRouter` and its underlying DEX contracts.

### Citations

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

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L33-36)
```go
var GaslessSwapRouterMetaData = &bind.MetaData{
	ABI: "[{\"inputs\":[{\"internalType\":\"address\",\"name\":\"_wkaia\",\"type\":\"address\"}],\"stateMutability\":\"nonpayable\",\"type\":\"constructor\"},{\"anonymous\":false,\"inputs\":[{\"indexed\":false,\"internalType\":\"uint256\",\"name\":\"amount\",\"type\":\"uint256\"}],\"name\":\"CommissionClaimed\",\"type\":\"event\"},{\"anonymous\":false,\"inputs\":[{\"indexed\":false,\"internalType\":\"uint256\",\"name\":\"oldRate\",\"type\":\"uint256\"},{\"indexed\":false,\"internalType\":\"uint256\",\"name\":\"newRate\",\"type\":\"uint256\"}],\"name\":\"CommissionRateUpdated\",\"type\":\"event\"},{\"anonymous\":false,\"inputs\":[{\"indexed\":true,\"internalType\":\"address\",\"name\":\"previousOwner\",\"type\":\"address\"},{\"indexed\":true,\"internalType\":\"address\",\"name\":\"newOwner\",\ ... (truncated)
	Bin: "0x60a0346100e557601f6200198738819003918201601f19168301916001600160401b038311848410176100ea578084926020946040528339810103126100e557516001600160a01b038116908190036100e55761005b33610100565b80156100a05761006a33610100565b608052600060035560405161183f908162000148823960805181818161044a01528181610a7301528181610fb201526112960152f35b60405162461bcd60e51b815260206004820152601b60248201527f5a65726f2061646472657373206973206e6f7420616c6c6f77656400000000006044820152606490fd5b600080fd5b634e487b7160e01b600052604160045260246000fd5b600080546001600160a01b039283166001600160a01b03198216811783559216907f8be0079c531659141344cd1fd0a4f28419497f9722a3daafe3b4186f6b6457e09080a356fe60406080815260048036101561001f575b5050361561001d57600080fd5b005b600091823560e01c8062fa3d50146112ba578063145d51d814611276578063161efb621 ... (truncated)
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L301-330)
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

// GetAmountIn is a free data retrieval call binding the contract method 0x632db21c.
//
// Solidity: function getAmountIn(address token, uint256 amountOut) view returns(uint256 amountIn)
func (_GaslessSwapRouter *GaslessSwapRouterSession) GetAmountIn(token common.Address, amountOut *big.Int) (*big.Int, error) {
	return _GaslessSwapRouter.Contract.GetAmountIn(&_GaslessSwapRouter.CallOpts, token, amountOut)
}

// GetAmountIn is a free data retrieval call binding the contract method 0x632db21c.
//
// Solidity: function getAmountIn(address token, uint256 amountOut) view returns(uint256 amountIn)
func (_GaslessSwapRouter *GaslessSwapRouterCallerSession) GetAmountIn(token common.Address, amountOut *big.Int) (*big.Int, error) {
	return _GaslessSwapRouter.Contract.GetAmountIn(&_GaslessSwapRouter.CallOpts, token, amountOut)
}
```

**File:** params/kip71_config.go (L58-68)
```go
func (kc *KIP71Config) NextMagmaBlockBaseFee(parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) *big.Int {
	// governance parameters
	lowerBoundBaseFee := new(big.Int).SetUint64(kc.LowerBoundBaseFee)
	upperBoundBaseFee := new(big.Int).SetUint64(kc.UpperBoundBaseFee)
	makeEvenByCeil(lowerBoundBaseFee)
	makeEvenByFloor(upperBoundBaseFee)

	// If the parent is the magma disabled block or genesis, then return the lowerBoundBaseFee (default 25ston)
	if parentHeaderNumber.Cmp(new(big.Int).SetUint64(0)) == 0 || parentHeaderBaseFee == nil {
		return makeEvenByFloor(lowerBoundBaseFee)
	}
```

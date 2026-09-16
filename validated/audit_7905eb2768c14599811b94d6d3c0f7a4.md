### Title
Gasless swap admission check relies on a manipulable spot AMM price, enabling reserve manipulation to bypass the node's repayment-sufficiency check - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The `kaiax/gasless` module allows an unprivileged, gas-less transaction sender to submit a `SwapForGas` transaction that a Kaia node validates and subsidizes gas for, based on a price read live from a DEX pool via `GaslessSwapRouter.GetAmountIn`. This spot-price read is the same class of "misuse the balances of assets in the pool to directly calculate price" flaw that caused the Inverse Finance exploit: an unprivileged actor can move the pool's reserves in a preceding transaction within the same block to change what `GetAmountIn` returns, before the node's admission check consumes it.

### Finding Description
When a `GaslessSwapTx` is submitted, the module's `GetCheckBalance` callback invokes `checkBalanceForSwap`, which computes the "required" input amount purely from the DEX pool's current reserves: [1](#0-0) 

This mirrors `GaslessSwapRouter.GetAmountIn`, whose Solidity implementation is not vendored in this repository (only Go bindings exist), but is documented to be backed by a Uniswap-V2-style reserve-based `getAmountIn(amountOut, reserveIn, reserveOut)` formula: [2](#0-1) 

The only invariant enforced independently of the pool state is `minAmountOut >= amountRepay`, which is a user-supplied value, not a value re-derived from a manipulation-resistant price source: [3](#0-2) 

Because `requiredAmountIn` is derived from the pool's live reserves at the moment the check executes (via `backends.NewBlockchainContractBackend` reading current chain state), a sender can:
1. Submit or have included (in the same bundle/block) a transaction that swaps in the underlying Uniswap-style pool used by `GaslessSwapRouter`, pushing the reserve ratio in their favor.
2. Immediately follow with `GaslessApproveTx`/`GaslessSwapTx`, whose `checkBalanceForSwap` is evaluated against the now-manipulated reserves, so a smaller `amountIn`/`minAmountOut` pair than would otherwise be required is accepted by the node.
3. The gasless module's `LendTxGenerator` (an in-protocol lender that fronts native KAIA to pay for the sender's gas, per `kaiax/gasless/README.md`) advances funds expecting to be repaid `amountRepay` from the swap. If the manipulated-reserve check under-estimates the true amount needed, or the pool reverts to a normal price by execution time within the same block, the sender's actual swap proceeds can be insufficient relative to what the node subsidized, without the module ever consulting a manipulation-resistant price (e.g., TWAP) or re-validating against post-execution state.

This is structurally identical to the reachable bug class in the reference report: an unprivileged caller (submitted transaction sender) manipulates AMM pool balances that are directly read as "price" by an on-chain/off-chain settlement check, rather than using a robust oracle.

Additionally, this check can be disabled entirely via configuration (`ShouldCheckSwapAmount`), which — if misconfigured — removes even this weak protection: [4](#0-3) 

### Impact Explanation
The gasless/fee-delegation flow (`LendTxGenerator` + `GaslessApproveTx` + `GaslessSwapTx` bundle, per `kaiax/auction`/`kaiax/gasless` docs) moves real KAIA value on behalf of an unprivileged sender before that sender's swap proceeds are confirmed to be sufficient using a price source that is not manipulation-resistant. An attacker able to move the pool's reserves (a single unprivileged action reachable via ordinary DEX swap transactions) can cause the node's admission check to under-value the required repayment, resulting in gasless/fee-delegation settlement abuse (subsidized gas paid without adequate repayment) — this falls squarely within the accepted "gasless or auction settlement theft" impact class.

### Likelihood Explanation
Reaching this path requires only submitting ordinary transactions: a DEX swap transaction and a `GaslessApproveTx`/`GaslessSwapTx` transaction, all of which any public RPC caller/tx sender can construct. No validator, node-operator, or cryptographic-key privilege is required. The primary uncertainty is the exact on-chain slippage enforcement inside `GaslessSwapRouter.sol` (its Solidity source is not present in this repository — only ABI/bindings are indexed), so I cannot confirm whether the live contract additionally re-checks `minAmountOut` at execution time in a way that would fully neutralize the node-side check bypass. This should be verified against the actual deployed `GaslessSwapRouter` contract source.

### Recommendation
- Derive `requiredAmountIn` (and any repayment-sufficiency check) from a manipulation-resistant price source (e.g., TWAP over multiple blocks, or a bound on maximum single-block price deviation) rather than the pool's instantaneous reserves.
- Re-validate `checkBalanceForSwap` against the exact state immediately preceding the bundle's execution within block building, not only at tx-pool admission, and reject/drop bundles whose actual on-chain output would be insufficient to cover `amountRepay`.
- Consider bounding how much the reserve ratio may have moved between admission-time check and execution-time inclusion, rejecting the bundle if deviation exceeds a safe threshold.
- Do not allow `ShouldCheckSwapAmount` to be disabled in production configurations without an equivalent alternative safeguard.

### Proof of Concept
1. Attacker (or colluding party) holds a controlling position or sufficient capital to swap in the underlying DEX pool referenced by `GaslessSwapRouter` for a given token.
2. Attacker sends a large swap transaction against that pool to shift reserves favorably.
3. In the same block (or immediately following, before reserves revert), attacker's own account submits `GaslessApproveTx` + `GaslessSwapTx` with `amountIn`/`minAmountOut` computed against the manipulated reserves, passing `checkBalanceForSwap`'s `requiredAmountIn := routerContract.GetAmountIn(...)` check (`kaiax/gasless/impl/tx_pool.go:128-141`).
4. The `LendTxGenerator` advances gas payment based on this passing check; if the swap's actual settled value (once reserves normalize or due to intra-block ordering) is less than `amountRepay`, the gasless subsidy is under-collateralized — reproducing the "misuse the balances of assets in the pool to directly calculate price" root cause from the referenced Inverse Finance exploit.

Note: full confirmation of the magnitude of exploitability depends on the on-chain `GaslessSwapRouter.sol` logic (not present in this repo's indexed contents, only its Go bindings), which should be reviewed directly to confirm whether execution-time slippage protection independently prevents value loss.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L115-120)
```go
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

**File:** contracts/bindings/uniswap/router/UniswapV2Router02.go (L3314-3327)
```go
// GetAmountIn is a free data retrieval call binding the contract method 0x85f8c259.
//
// Solidity: function getAmountIn(uint256 amountOut, uint256 reserveIn, uint256 reserveOut) pure returns(uint256 amountIn)
func (_IUniswapV2Router01 *IUniswapV2Router01Caller) GetAmountIn(opts *bind.CallOpts, amountOut *big.Int, reserveIn *big.Int, reserveOut *big.Int) (*big.Int, error) {
	var out []interface{}
	err := _IUniswapV2Router01.contract.Call(opts, &out, "getAmountIn", amountOut, reserveIn, reserveOut)
	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err
}
```

### Title
Gasless swap admission relies on manipulable single-block AMM spot price - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The `checkBalanceForSwap` admission check in the gasless module prices a whitelisted ERC20 token purely from the `GaslessSwapRouter.GetAmountIn` view call, which itself is a spot-reserve quote against the underlying Uniswap-V2-style DEX pair. This is the same bug class as the reported LP/TVL spot-pricing issue: a value used for a security-relevant decision is derived from instantaneously manipulable AMM reserves, with no TWAP or manipulation-resistance, and can be skewed within one block at negligible risk to the manipulator.

### Finding Description
`GaslessModule.checkBalanceForSwap` requires `swapArgs.AmountIn >= requiredAmountIn`, where `requiredAmountIn` is fetched live from the whitelisted `GaslessSwapRouter` contract via `GetAmountIn(token, minAmountOut)`: [1](#0-0) 

The `GaslessSwapRouter.GetAmountIn` function is a `view` call bound in `contracts/bindings/kip247/GaslessSwapRouter.go` that, per the module's DEX/factory/router linkage (`getDEXInfo`/`dexAddress`), quotes the token amount by reading the associated Uniswap-V2-style pair's `getReserves()` and applying the constant-product formula — an instantaneous spot price with no time-weighting: [2](#0-1) [3](#0-2) 

This is architecturally identical to the reported bug class: a value (LP TVL/token price in the report; token exchange rate here) is computed from raw pool reserves at the moment of the call, and those reserves can be trivially inflated or deflated within the same block by depositing or swapping large amounts into the pool, with the manipulation fully reversible (e.g., via a flash swap or back-to-back trade) at negligible cost. Because reserves are queried live and not through a manipulation-resistant oracle (TWAP, external price feed, or liquidity-weighted median), any unprivileged party who can also touch the underlying DEX pool (which is not access-restricted) can shift the `GetAmountIn` result up or down within the block that a gasless `SwapForGas` transaction is processed.

### Impact Explanation
`checkBalanceForSwap` is used as `GetCheckBalance`, the tx-pool admission gate that decides whether a `swapForGas` transaction is accepted as a valid gasless transaction and bundled by the block builder (`ExtractTxBundles`) alongside a proposer-funded lend transaction. If the spot price is manipulated downward at the moment of admission/bundling, `requiredAmountIn` can be made artificially small, allowing a transaction with insufficient real backing collateral (`AmountIn`) to be treated as valid gasless-eligible and included in a bundle where the proposer/network fronts the gas (via the lend transaction) expecting to be repaid `amountRepay` from the swap's proceeds. If the pool state reverts (attacker unwinds the manipulation) before or during on-chain execution of the actual `swapForGas` call, the resulting swap can yield less than `amountRepay`, causing reward/fee redirection or fronted-gas loss to the proposer, or forcing an on-chain revert that wastes proposer-fronted gas allowance while still consuming block space allocated to the gasless bundle. This is reachable purely by a gasless-swap tx sender colluding with (or acting as) a party who can move the underlying DEX pool's reserves in the same block — no privileged/consensus role required.

### Likelihood Explanation
Likelihood is Medium: the underlying DEX pools referenced by `dexAddress`/`getDEXInfo` are ordinary, permissionless AMM pairs, so any account can submit swap/liquidity transactions that shift reserves. Coordinating a reserve-manipulating transaction and a `swapForGas` transaction within the same block (or adjacent blocks before `PostInsertBlock` price re-sync) is realistic for a searcher-style actor, especially since the auction/bundle mechanisms in this codebase already demonstrate infrastructure for same-block transaction ordering. The check is re-evaluated at admission time using current chain state, so the attack window is exactly one block per manipulation attempt, consistent with the "no risk, 1-block" characteristic called out in the original report.

### Recommendation
Do not rely solely on the live spot-reserve quote (`getReserves`-derived `GetAmountIn`) for the swap admission/collateral check. Use a manipulation-resistant price source such as a TWAP over multiple blocks, a bounded-deviation check against an external oracle, or enforce a maximum single-block price deviation before accepting `requiredAmountIn`. Additionally, consider re-validating the required amount at execution time inside the on-chain `swapForGas` function against a reference price rather than trusting the pool's instantaneous reserves at both admission and execution.

### Proof of Concept
1. Attacker identifies a whitelisted gasless token `T` whose DEX pair has moderate liquidity and is used by `GaslessSwapRouter.GetAmountIn`.
2. Attacker submits (or arranges) a large swap/deposit into the `T`/base-asset pool in the same block, shifting reserves so that `GetAmountIn(T, minAmountOut)` returns an artificially low `requiredAmountIn`.
3. Attacker (or colluding sender) submits a `swapForGas` transaction with `AmountIn` just above this manipulated `requiredAmountIn`, which passes `checkBalanceForSwap`'s `ShouldCheckSwapAmount` gate: [1](#0-0) 
4. The gasless tx is admitted to the pool and bundled with a proposer-funded lend transaction for gas.
5. Before or during on-chain execution, the pool reserves are restored (attacker's manipulating trade reverses), so the actual `swapForGas` execution converts `AmountIn` for materially less output than assumed at admission time, resulting in insufficient repayment of `amountRepay` to the proposer/protocol.

### Citations

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

**File:** contracts/bindings/uniswap/router/UniswapV2Router02.go (L3953-3966)
```go
// GetAmountIn is a free data retrieval call binding the contract method 0x85f8c259.
//
// Solidity: function getAmountIn(uint256 amountOut, uint256 reserveIn, uint256 reserveOut) pure returns(uint256 amountIn)
func (_IUniswapV2Router02 *IUniswapV2Router02Caller) GetAmountIn(opts *bind.CallOpts, amountOut *big.Int, reserveIn *big.Int, reserveOut *big.Int) (*big.Int, error) {
	var out []interface{}
	err := _IUniswapV2Router02.contract.Call(opts, &out, "getAmountIn", amountOut, reserveIn, reserveOut)
	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err
}
```

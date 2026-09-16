## Analysis

The Sherlock report flags **reliance on an instantaneous, single-block AMM price (`slot0`) instead of a TWAP** as the root cause of a price-manipulation vulnerability. The closest reachable analog in `kaia--002` is the **KIP‑247 gasless-transaction settlement path**, where both the mempool admission check and the on-chain swap execution price a token swap using the *current* (spot) reserve ratio of a UniswapV2-style pool rather than any time-weighted or otherwise manipulation-resistant price source.

### Root cause

`GaslessModule.checkBalanceForSwap` (mempool/tx-pool admission check for a `GaslessSwapTx`) determines the required `amountIn` by calling `GaslessSwapRouter.GetAmountIn(token, minAmountOut)`, which is a live, on-chain call into the router/DEX contract at the current block state: [1](#0-0) 

The `GaslessSwapRouter` contract itself resolves per-token DEX routing via `dexAddress`/`getDEXInfo` and computes `getAmountIn` against that DEX's live reserves: [2](#0-1) 

The actual swap settlement (`SwapForGas`) executes against the same DEX at execution time and derives `FinalUserAmount`/`AmountRepaid`/`Commission` from that swap's output: [3](#0-2) [4](#0-3) 

Both the pool-admission `minAmountOut`/`amountIn` check and the settlement swap itself are priced off the DEX's instantaneous reserve ratio (`getReserves`/`getAmountsOut`/`getAmountIn` of `UniswapV2Router02`/`UniswapV2Pair`) — the same class of unprotected spot-price read as `slot0` in the reported issue, with no TWAP or manipulation-resistant oracle in the KIP-247 flow. [5](#0-4) 

### Reachability and impact

Any unprivileged party can submit ordinary transactions to the same DEX pool used by `GaslessSwapRouter` (sandwich/front-run/back-run) within the same block that contains a pending `GaslessApproveTx`+`GaslessSwapTx` bundle, since these bundles are simply prepended with a `LendTxGenerator` and placed in the block by the proposer without any TWAP-based price protection: [6](#0-5) 

By manipulating the pool reserves immediately before the gasless swap executes (and restoring them afterward within the same block), an attacker can force the `swapForGas` execution to settle at a manipulated price. Because `minAmountOut` only needs to exceed `amountRepay` (checked against the *current, manipulable* `GetAmountIn`), the manipulated spot price can be used to:
- extract value from the gasless user's swap output (reducing `FinalUserAmount`), and/or
- cause `AmountRepaid` (the amount that repays the block proposer's gas lending) to fall short relative to real value, since it is computed purely from the manipulable swap execution rather than a robust price feed: [7](#0-6) 

This matches the report's essence (unprotected spot-price dependency enabling manipulation) mapped onto a Kaia system reachable purely by a public transaction sender / MEV bundler, involving fee-delegation-style value transfer (block-proposer gas repayment) and gasless settlement — squarely inside the allowed scope ("gasless and auction modules").

### Title
Gasless swap settlement (KIP-247) relies on manipulable spot AMM reserves instead of a TWAP, enabling sandwich-based value extraction from `GaslessSwapRouter.swapForGas` - (File: kaiax/gasless/impl/tx_pool.go, contracts/bindings/kip247/GaslessSwapRouter.go)

### Summary
The gasless transaction flow (KIP-247) prices both mempool admission (`checkBalanceForSwap`) and on-chain settlement (`swapForGas`) of a `GaslessSwapTx` using the *current* reserve ratio of the underlying DEX pool. There is no TWAP or other manipulation-resistant price check, mirroring the reported `slot0`-based spot-price vulnerability.

### Finding Description
`checkBalanceForSwap` validates `swapArgs.AmountIn` against `GetAmountIn(token, minAmountOut)`, a live call into `GaslessSwapRouter`, which in turn queries the DEX (`dexAddress`/`getDEXInfo`) for its live reserves (`kaiax/gasless/impl/tx_pool.go:128-141`; `contracts/bindings/kip247/GaslessSwapRouter.go:270-330`). The final settlement (`swapForGas`) executes the swap against the same pool at execution time, emitting `SwappedForGas` with `FinalUserAmount`/`AmountRepaid`/`Commission` derived from that spot execution (`contracts/bindings/kip247/GaslessSwapRouter.go:554-566, 1127-1136`). No component of this pipeline references a time-weighted average price; both admission and settlement trust the instantaneous pool state, exactly as `USSDRebalancer.getOwnValuation()` trusted `slot0`.

### Impact Explanation
An attacker who can submit ordinary transactions against the DEX pool backing a whitelisted gasless token can sandwich a pending gasless bundle within the same block (block assembly places `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` sequentially per `kaiax/gasless/README.md:29-36`) to skew reserves immediately before `swapForGas` executes and restore them afterward. This manipulates the effective swap price used both for the tx-pool `amountIn` sufficiency check and for the real settlement, allowing extraction of value from the user's swap output and/or the block proposer's gas-fee repayment (`AmountRepaid`), constituting fee-delegation/gasless-settlement value theft.

### Likelihood Explanation
Medium: this requires the attacker to control (or influence via ordinary swap transactions) the liquidity of the specific DEX pool registered for the gasless token, and to time transactions within the same block as the gasless bundle — both are within reach of any unprivileged transaction sender/MEV bundler with no special privileges, similar to typical AMM sandwich conditions.

### Recommendation
Do not rely solely on the DEX's instantaneous reserves for pricing `GaslessSwapTx` admission or settlement. Introduce a TWAP-based or otherwise manipulation-resistant price check (or a maximum allowed deviation between spot and a reference price) before both admitting a `GaslessSwapTx` into the pool and before finalizing `swapForGas`, and/or require minimum liquidity/slippage bounds tighter than raw spot pricing.

### Proof of Concept
1. Attacker identifies a pending `GaslessApproveTx`+`GaslessSwapTx` pair for token `T` in the mempool (visible via public RPC).
2. In the same block, attacker front-runs with a large swap on `T`'s DEX pool to shift reserves unfavorably for the gasless swap direction.
3. The proposer's block assembly places `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` immediately after, per `kaiax/gasless/README.md:29-36`; `swapForGas` executes against the skewed reserves, computing a worse `FinalUserAmount`/`AmountRepaid` than the true market price.
4. Attacker back-runs to restore reserves and pocket the arbitrage difference, extracted from the gasless settlement path described in `kaiax/gasless/impl/tx_pool.go:102-182` and `contracts/bindings/kip247/GaslessSwapRouter.go:554-566`.

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

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L270-330)
```go
// DexAddress is a free data retrieval call binding the contract method 0xe3bcccb4.
//
// Solidity: function dexAddress(address token) view returns(address)
func (_GaslessSwapRouter *GaslessSwapRouterCaller) DexAddress(opts *bind.CallOpts, token common.Address) (common.Address, error) {
	var out []interface{}
	err := _GaslessSwapRouter.contract.Call(opts, &out, "dexAddress", token)

	if err != nil {
		return *new(common.Address), err
	}

	out0 := *abi.ConvertType(out[0], new(common.Address)).(*common.Address)

	return out0, err

}

// DexAddress is a free data retrieval call binding the contract method 0xe3bcccb4.
//
// Solidity: function dexAddress(address token) view returns(address)
func (_GaslessSwapRouter *GaslessSwapRouterSession) DexAddress(token common.Address) (common.Address, error) {
	return _GaslessSwapRouter.Contract.DexAddress(&_GaslessSwapRouter.CallOpts, token)
}

// DexAddress is a free data retrieval call binding the contract method 0xe3bcccb4.
//
// Solidity: function dexAddress(address token) view returns(address)
func (_GaslessSwapRouter *GaslessSwapRouterCallerSession) DexAddress(token common.Address) (common.Address, error) {
	return _GaslessSwapRouter.Contract.DexAddress(&_GaslessSwapRouter.CallOpts, token)
}

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

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L554-566)
```go
// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) SwapForGas(opts *bind.TransactOpts, token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "swapForGas", token, amountIn, minAmountOut, amountRepay, deadline)
}

// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterSession) SwapForGas(token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.SwapForGas(&_GaslessSwapRouter.TransactOpts, token, amountIn, minAmountOut, amountRepay, deadline)
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L1127-1136)
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

**File:** kaiax/gasless/README.md (L1-41)
```markdown
# kaiax/gasless

This module is responsible for facilitating gasless transactions specified by [KIP-247](https://kips.kaia.io/KIPs/kip-247).

## Concepts

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

## Persistent schema

This module does not persist any data.

```

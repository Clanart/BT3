### Title
Owner can front-run `GaslessSwapRouter.updateCommissionRate` to steal value from user `swapForGas` gasless-swap transactions - (File: `contracts/bindings/kip247/GaslessSwapRouter.go`, underlying `GaslessSwapRouter.sol`)

### Summary
The KIP-247 gasless-swap system contract `GaslessSwapRouter` exposes an owner-only `updateCommissionRate(uint256 _commissionRate)` setter that changes the commission percentage taken from a user's `swapForGas` output with no timelock, bound, or cooldown. Because gasless swap transactions are bundled and executed by the block proposer shortly after being submitted to the pool, the contract owner can observe a pending `swapForGas` call and front-run it with `updateCommissionRate` to raise the commission just before the user's swap executes, extracting more value than the user expected — directly analogous to the reported `UniV3OracleImpl.setDefaultScaledOfferFactor` front-running issue.

### Finding Description
`GaslessSwapRouter` maintains a mutable `commissionRate` state variable, updated via the owner-gated `updateCommissionRate` transactor method [1](#0-0) , and emits `CommissionRateUpdated(oldRate, newRate)` [2](#0-1) . The rate is read at execution time and is subtracted from the swap output, as evidenced by the `SwappedForGas` event which reports `FinalUserAmount` and `Commission` computed during the same call [3](#0-2) .

Gasless transactions (`GaslessSwapTx`) are a first-class Kaia transaction flow: the `kaiax/gasless` module promotes them in the pool and the block-building logic prepends a `LendTxGenerator` transaction, bundling `[LendTxGenerator, (GaslessApproveTx), GaslessSwapTx]` for inclusion [4](#0-3) . Since the gas fee for these transactions is fronted by the proposer and repaid from the swap during `swapForGas` [5](#0-4) , the proposer (who may also be the router's owner or collude with the owner) sees the user's pending `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` call before inclusion [6](#0-5) . Because `updateCommissionRate` has no timelock or maximum-rate cap enforced in the binding, the owner can insert an `updateCommissionRate` call immediately before the victim's `swapForGas` transaction in the same block/bundle, raising the commission and capturing a larger share of the user's swapped funds while only `minAmountOut` — a DEX-slippage guard, not a commission guard — limits the user's downside.

### Impact Explanation
An owner (or proposer colluding with the owner) can unilaterally and instantly increase the commission taken from every pending gasless swap, redirecting user funds to the commission recipient (claimed via `CommissionClaimed`/`claimCommission`-type flow implied by the `CommissionClaimed(uint256 amount)` event [2](#0-1) ). This is unauthorized value extraction from unprivileged gasless-swap users reachable purely by submitting a normal `swapForGas` transaction, matching the "fee delegation abuse / gasless settlement theft" category.

### Likelihood Explanation
Likelihood is high in adversarial-owner or owner-proposer-collusion scenarios: gasless swaps are visible in the mempool/bundle prior to inclusion, `updateCommissionRate` requires only a single owner transaction with no delay, and there is no evidence of a rate cap, timelock, or slippage-style protection against commission changes in the exposed contract interface.

### Recommendation
Add a timelock (e.g., a minimum delay between calling `updateCommissionRate` and it taking effect) or restrict commission changes to only apply to swaps submitted after the change is finalized. Alternatively, let `swapForGas` accept a caller-specified maximum acceptable commission rate (analogous to `minAmountOut`) and revert if the current on-chain `commissionRate` exceeds it, preventing the owner from retroactively worsening terms for an already-submitted transaction.

### Proof of Concept
1. User submits `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` expecting the current `commissionRate` (e.g., low value) to apply [6](#0-5) .
2. Router owner observes the pending gasless-swap bundle and submits `updateCommissionRate(newHigherRate)` [1](#0-0) , which the proposer (potentially the same party) orders ahead of the user's transaction within the block/bundle.
3. When `swapForGas` executes, it reads the now-inflated `commissionRate`, causing `FinalUserAmount` to shrink and `Commission` to grow as reflected in the `SwappedForGas` event [3](#0-2) , with `minAmountOut` unable to protect against this commission-driven loss since it only bounds DEX price slippage, not the commission cut.

### Citations

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L568-573)
```go
// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactorSession) SwapForGas(token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.SwapForGas(&_GaslessSwapRouter.TransactOpts, token, amountIn, minAmountOut, amountRepay, deadline)
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L596-601)
```go
// UpdateCommissionRate is a paid mutator transaction binding the contract method 0x00fa3d50.
//
// Solidity: function updateCommissionRate(uint256 _commissionRate) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) UpdateCommissionRate(opts *bind.TransactOpts, _commissionRate *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "updateCommissionRate", _commissionRate)
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L839-844)
```go
// GaslessSwapRouterCommissionRateUpdated represents a CommissionRateUpdated event raised by the GaslessSwapRouter contract.
type GaslessSwapRouterCommissionRateUpdated struct {
	OldRate *big.Int
	NewRate *big.Int
	Raw     types.Log // Blockchain specific contextual infos
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

**File:** kaiax/gasless/README.md (L7-11)
```markdown
Gasless transaction (GaslessTx) consists of two types: gasless approve transaction (GaslessApproveTX), and gasless swap transaction (GaslessSwapTx).

Note that gasless transaction does not mean the gas prices of gasless transactions are zero, but it means that proposer will lend the user with gas fee and user will pay back during gasless swap.

The gas fee of gasless transaction's is funded by block proposer (i.e., lend transaction generated by `GetLendTxGenerator`), and user repays the lent amount during gasless swap.
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

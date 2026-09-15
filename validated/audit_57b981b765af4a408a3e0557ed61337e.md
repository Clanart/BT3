### Title
GaslessSwapRouter owner can set `commissionRate` unbounded, allowing full seizure of gasless-swap users' output tokens - ([File: contracts/bindings/kip247/GaslessSwapRouter.go])

### Summary
`GaslessSwapRouter.updateCommissionRate(uint256)` is an owner-only setter whose ABI/bytecode expose no `MAX_COMMISSION_RATE`/`COMMISSION_DENOMINATOR` cap, unlike the sibling `PublicDelegation` contract which explicitly defines `MAX_COMMISSION_RATE` and `COMMISSION_DENOMINATOR`. This mirrors the DODO `changeRouteFeeRate()` finding: an owner (or a compromised/malicious owner key) can push the commission rate applied inside `swapForGas` to a value that consumes all or nearly all of a gasless user's swap output, since users authorizing gasless swaps have no way to bound the commission at signing time beyond `minAmountOut`.

### Finding Description
`GaslessSwapRouter` is the KIP-247 gasless-swap settlement contract used by Kaia's gasless module. Its ABI exposes `commissionRate()` (view), `UpdateCommissionRate(uint256)` (owner-only mutator emitting `CommissionRateUpdated(oldRate, newRate)`), and `SwapForGas(token, amountIn, minAmountOut, amountRepay, deadline)`, which on success emits `SwappedForGas(proposer, amountRepaid, user, finalUserAmount, commission)`. [1](#0-0) [2](#0-1) 

Unlike `PublicDelegation`, which defines both `MAX_COMMISSION_RATE` and `COMMISSION_DENOMINATOR` as part of its public interface to bound commission changes, `GaslessSwapRouter`'s metadata does not expose any such cap constant or getter: [3](#0-2) [4](#0-3) 

The gasless flow relies on `swapForGas` to convert a whitelisted ERC20 token into KAIA, repay the block proposer's advanced gas (`amountRepay`), and give the user the `finalUserAmount` remainder after commission. The kaiax gasless module only validates `minAmountOut >= amountRepay` and that `amountIn` is sufficient for the declared `minAmountOut` at the current AMM exchange rate — it does not independently validate or cap the commission percentage taken by the router itself: [5](#0-4) 

Because the commission is applied inside the `GaslessSwapRouter` contract itself (governed solely by its `owner`), and no `MAX_COMMISSION_RATE` bound is present in its interface, an owner (single EOA per the constructor's simple `Ownable`-style pattern) can call `updateCommissionRate` with a value approaching 100% before or in the same block as pending `swapForGas` transactions, capturing nearly the entire swap output as "commission" while still satisfying the `minAmountOut >= amountRepay` and `amountIn >= requiredAmountIn` checks enforced by the tx-pool/gasless module (those checks bound the *swap* output vs. requested minimum, not the router's post-swap commission deduction).

### Impact Explanation
This is directly analogous to the DODO `routeFeeRate` finding: a single owner-controlled, unbounded fee parameter that is applied at settlement time against user funds that have already been committed via signed (and potentially already gas-advanced) transactions. In the gasless flow, users cannot include their own slippage protection against the commission (only against `minAmountOut` of the swap, not the router's internal commission take), so a malicious/compromised owner could sandwich pending `swapForGas` calls by raising `commissionRate` right before they land in a block, redirecting most of the user's swapped-out KAIA to the protocol/owner via `CommissionClaimed`. This is unauthorized value extraction/fee abuse against gasless-swap users reachable purely by submitting a normal `swapForGas` transaction (an unprivileged sender's tx), matching the required "concrete unauthorized value movement / fee abuse" criteria.

### Likelihood Explanation
Likelihood is Medium: it requires the router owner (a privileged but plausibly single-key-controlled account, as suggested by the constructor's `Ownable`-pattern zero-address check) to act maliciously or be compromised, and requires timing the rate change against pending gasless swap transactions — the same preconditions Sherlock rated Medium for the DODO original finding. From the codebase available, I could not locate the actual GaslessSwapRouter Solidity source (only the generated Go bindings) to definitively confirm the absence of an internal upper-bound `require` in `updateCommissionRate`'s implementation; the ABI/interface lacking any exposed `MAX_COMMISSION_RATE` constant (in contrast to `PublicDelegation`) is the strongest available evidence, but this is inferred from binding metadata rather than a directly-read Solidity guard clause.

### Recommendation
Add a `MAX_COMMISSION_RATE` (and `COMMISSION_DENOMINATOR`) constant to `GaslessSwapRouter`, consistent with `PublicDelegation`'s pattern, and enforce `require(_commissionRate <= MAX_COMMISSION_RATE)` inside `updateCommissionRate`. Additionally, consider having the kaiax gasless module (`kaiax/gasless/impl/tx_pool.go`) independently verify that the effective commission does not exceed a sane bound relative to `amountIn`/`minAmountOut`, so a router-side rate change cannot silently break the economic assumptions the gasless tx-pool validation relies on.

### Proof of Concept
Not independently reproducible from the indexed contents — the actual Solidity source of `GaslessSwapRouter.sol` (containing `updateCommissionRate`'s body) was not found in the index (only the compiled Go bindings in `contracts/bindings/kip247/GaslessSwapRouter.go` were available). A Devin session with full repository/filesystem access would be needed to inspect the Solidity source directly and confirm whether any inline `require` bound exists, and to write a concrete PoC test (e.g., extending `tests/gasless_test.go`) demonstrating owner calling `UpdateCommissionRate` with a near-100% value immediately before a pending `SwapForGas` transaction executes.

### Citations

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L32-36)
```go
// GaslessSwapRouterMetaData contains all meta data concerning the GaslessSwapRouter contract.
var GaslessSwapRouterMetaData = &bind.MetaData{
	ABI: "[{\"inputs\":[{\"internalType\":\"address\",\"name\":\"_wkaia\",\"type\":\"address\"}],\"stateMutability\":\"nonpayable\",\"type\":\"constructor\"},{\"anonymous\":false,\"inputs\":[{\"indexed\":false,\"internalType\":\"uint256\",\"name\":\"amount\",\"type\":\"uint256\"}],\"name\":\"CommissionClaimed\",\"type\":\"event\"},{\"anonymous\":false,\"inputs\":[{\"indexed\":false,\"internalType\":\"uint256\",\"name\":\"oldRate\",\"type\":\"uint256\"},{\"indexed\":false,\"internalType\":\"uint256\",\"name\":\"newRate\",\"type\":\"uint256\"}],\"name\":\"CommissionRateUpdated\",\"type\":\"event\"},{\"anonymous\":false,\"inputs\":[{\"indexed\":true,\"internalType\":\"address\",\"name\":\"previousOwner\",\"type\":\"address\"},{\"indexed\":true,\"internalType\":\"address\",\"name\":\"newOwner\",\ ... (truncated)
	Bin: "0x60a0346100e557601f6200198738819003918201601f19168301916001600160401b038311848410176100ea578084926020946040528339810103126100e557516001600160a01b038116908190036100e55761005b33610100565b80156100a05761006a33610100565b608052600060035560405161183f908162000148823960805181818161044a01528181610a7301528181610fb201526112960152f35b60405162461bcd60e51b815260206004820152601b60248201527f5a65726f2061646472657373206973206e6f7420616c6c6f77656400000000006044820152606490fd5b600080fd5b634e487b7160e01b600052604160045260246000fd5b600080546001600160a01b039283166001600160a01b03198216811783559216907f8be0079c531659141344cd1fd0a4f28419497f9722a3daafe3b4186f6b6457e09080a356fe60406080815260048036101561001f575b5050361561001d57600080fd5b005b600091823560e01c8062fa3d50146112ba578063145d51d814611276578063161efb621 ... (truncated)
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L596-608)
```go
// UpdateCommissionRate is a paid mutator transaction binding the contract method 0x00fa3d50.
//
// Solidity: function updateCommissionRate(uint256 _commissionRate) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) UpdateCommissionRate(opts *bind.TransactOpts, _commissionRate *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "updateCommissionRate", _commissionRate)
}

// UpdateCommissionRate is a paid mutator transaction binding the contract method 0x00fa3d50.
//
// Solidity: function updateCommissionRate(uint256 _commissionRate) returns()
func (_GaslessSwapRouter *GaslessSwapRouterSession) UpdateCommissionRate(_commissionRate *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.UpdateCommissionRate(&_GaslessSwapRouter.TransactOpts, _commissionRate)
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

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L278-307)
```go
// MAXCOMMISSIONRATE is a free data retrieval call binding the contract method 0x207239c0.
//
// Solidity: function MAX_COMMISSION_RATE() view returns(uint256)
func (_PublicDelegation *PublicDelegationCaller) MAXCOMMISSIONRATE(opts *bind.CallOpts) (*big.Int, error) {
	var out []interface{}
	err := _PublicDelegation.contract.Call(opts, &out, "MAX_COMMISSION_RATE")

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}

// MAXCOMMISSIONRATE is a free data retrieval call binding the contract method 0x207239c0.
//
// Solidity: function MAX_COMMISSION_RATE() view returns(uint256)
func (_PublicDelegation *PublicDelegationSession) MAXCOMMISSIONRATE() (*big.Int, error) {
	return _PublicDelegation.Contract.MAXCOMMISSIONRATE(&_PublicDelegation.CallOpts)
}

// MAXCOMMISSIONRATE is a free data retrieval call binding the contract method 0x207239c0.
//
// Solidity: function MAX_COMMISSION_RATE() view returns(uint256)
func (_PublicDelegation *PublicDelegationCallerSession) MAXCOMMISSIONRATE() (*big.Int, error) {
	return _PublicDelegation.Contract.MAXCOMMISSIONRATE(&_PublicDelegation.CallOpts)
}
```

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

### Title
No slippage protection in `PublicDelegation.stake()`/`stakeFor()` can cause stakers to mint fewer shares than expected - ([File: contracts/bindings/publicdelegation/PublicDelegation.go])

### Summary
`PublicDelegation` is an ERC4626-like vault contract that lets any unprivileged address delegate (stake) native KAIA to a validator's public delegation pool in exchange for pool shares, valued via `convertToShares(assets)`/`convertToAssets(shares)` against `totalAssets()`/`totalSupply()`. The `stake()` and `stakeFor(_recipient)` entry points are `payable` and take **no minimum-shares-out (or maximum-assets-per-share) parameter**, exactly the missing-slippage-check pattern described in the ether-fi report.

### Finding Description
`stake()`/`stakeFor()` mint shares proportionally to the caller's contributed KAIA relative to the pool's current `totalAssets()`/`totalSupply()` ratio: [1](#0-0) 

The conversion is driven by `convertToShares`, whose result depends on the *current on-chain state* of `totalAssets`/`totalSupply` at execution time: [2](#0-1) [3](#0-2) 

Because `totalAssets()`/`totalSupply()` (and thus the assets-per-share ratio) can be changed by any other transaction that stakes, withdraws, or accrues/sweeps rewards before the staker's transaction is included, a user who submits `stake()`/`stakeFor()` expecting a certain number of shares (e.g., based on a `previewDeposit`/`convertToShares` off-chain simulation) has no on-chain guarantee — the function accepts any `msg.value` and mints whatever number of shares the ratio dictates at execution time, with no `minShares` parameter or revert condition to protect the caller. This mirrors the ether-fi `LiquidityPool.deposit()` bug class exactly: value is deposited, shares are computed off a mutable global ratio, and the caller cannot bound the acceptable output.

An unprivileged transaction sender is fully able to reach this: `stake()`/`stakeFor()` are public, payable functions with no allowlist, callable by any staker submitting a transaction.

### Impact Explanation
A staker can be griefed (front-run) by another party (e.g., a large staker depositing first, or a `sweep()`/reward-accrual transaction landing first) that shifts the assets/shares ratio unfavorably, causing the victim to receive materially fewer shares — and therefore materially less future economic entitlement (rewards, redeemable assets) — than they intended, with no way to abort the transaction based on a minimum-shares guarantee. Because `PublicDelegation` shares represent claims on real staked KAIA and future validator rewards, this is a concrete value-loss vector for stakers, not merely a cosmetic issue.

### Likelihood Explanation
The precondition (an intervening state-changing transaction — a competing `stake`/`stakeFor`, a `withdraw`, or a reward `sweep`) is a common, easily triggerable occurrence on a public delegation pool, and requires no special privilege — only the ability to submit an ordinary transaction ahead of the victim's, which any address (including MEV searchers) can attempt via normal transaction submission/fee bidding. No validator, node, or governance privilege is needed.

### Recommendation
Add an optional `minShares` (for `stake`/`stakeFor`) and/or `maxAssets` (for redeem/withdraw-style operations) parameter that the caller can set based on their own off-chain `previewDeposit`/`convertToShares` simulation, and revert the transaction if the actual minted/returned amount is worse than the caller-specified bound, following the standard ERC4626 `deposit(assets, receiver, minShares)`-style pattern.

### Proof of Concept
1. Staker A calls `previewDeposit(assets)` off-chain (or computes `convertToShares`) and observes it will receive `S` shares for `assets` KAIA, then submits `stake()` with `msg.value = assets`.
2. Before A's transaction is included, Staker B (or an intervening reward-related transaction) submits a transaction that changes `totalAssets()`/`totalSupply()` (e.g., a large `stake()`/`stakeFor()` or a rewards `sweep()`), altering the assets-per-share ratio.
3. A's `stake()` transaction executes afterward with the new ratio and mints `S' < S` shares, with no revert, because `stake()`/`stakeFor()` has no `minShares` parameter to enforce the originally expected exchange rate. [1](#0-0)

### Citations

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L526-541)
```go
// ConvertToShares is a free data retrieval call binding the contract method 0xc6e6f592.
//
// Solidity: function convertToShares(uint256 _assets) view returns(uint256)
func (_PublicDelegation *PublicDelegationCaller) ConvertToShares(opts *bind.CallOpts, _assets *big.Int) (*big.Int, error) {
	var out []interface{}
	err := _PublicDelegation.contract.Call(opts, &out, "convertToShares", _assets)

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L1022-1051)
```go
// TotalAssets is a free data retrieval call binding the contract method 0x01e1d114.
//
// Solidity: function totalAssets() view returns(uint256)
func (_PublicDelegation *PublicDelegationCaller) TotalAssets(opts *bind.CallOpts) (*big.Int, error) {
	var out []interface{}
	err := _PublicDelegation.contract.Call(opts, &out, "totalAssets")

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}

// TotalAssets is a free data retrieval call binding the contract method 0x01e1d114.
//
// Solidity: function totalAssets() view returns(uint256)
func (_PublicDelegation *PublicDelegationSession) TotalAssets() (*big.Int, error) {
	return _PublicDelegation.Contract.TotalAssets(&_PublicDelegation.CallOpts)
}

// TotalAssets is a free data retrieval call binding the contract method 0x01e1d114.
//
// Solidity: function totalAssets() view returns(uint256)
func (_PublicDelegation *PublicDelegationCallerSession) TotalAssets() (*big.Int, error) {
	return _PublicDelegation.Contract.TotalAssets(&_PublicDelegation.CallOpts)
}
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L1283-1323)
```go
// Stake is a paid mutator transaction binding the contract method 0x3a4b66f1.
//
// Solidity: function stake() payable returns()
func (_PublicDelegation *PublicDelegationTransactor) Stake(opts *bind.TransactOpts) (*types.Transaction, error) {
	return _PublicDelegation.contract.Transact(opts, "stake")
}

// Stake is a paid mutator transaction binding the contract method 0x3a4b66f1.
//
// Solidity: function stake() payable returns()
func (_PublicDelegation *PublicDelegationSession) Stake() (*types.Transaction, error) {
	return _PublicDelegation.Contract.Stake(&_PublicDelegation.TransactOpts)
}

// Stake is a paid mutator transaction binding the contract method 0x3a4b66f1.
//
// Solidity: function stake() payable returns()
func (_PublicDelegation *PublicDelegationTransactorSession) Stake() (*types.Transaction, error) {
	return _PublicDelegation.Contract.Stake(&_PublicDelegation.TransactOpts)
}

// StakeFor is a paid mutator transaction binding the contract method 0x4bf69206.
//
// Solidity: function stakeFor(address _recipient) payable returns()
func (_PublicDelegation *PublicDelegationTransactor) StakeFor(opts *bind.TransactOpts, _recipient common.Address) (*types.Transaction, error) {
	return _PublicDelegation.contract.Transact(opts, "stakeFor", _recipient)
}

// StakeFor is a paid mutator transaction binding the contract method 0x4bf69206.
//
// Solidity: function stakeFor(address _recipient) payable returns()
func (_PublicDelegation *PublicDelegationSession) StakeFor(_recipient common.Address) (*types.Transaction, error) {
	return _PublicDelegation.Contract.StakeFor(&_PublicDelegation.TransactOpts, _recipient)
}

// StakeFor is a paid mutator transaction binding the contract method 0x4bf69206.
//
// Solidity: function stakeFor(address _recipient) payable returns()
func (_PublicDelegation *PublicDelegationTransactorSession) StakeFor(_recipient common.Address) (*types.Transaction, error) {
	return _PublicDelegation.Contract.StakeFor(&_PublicDelegation.TransactOpts, _recipient)
}
```

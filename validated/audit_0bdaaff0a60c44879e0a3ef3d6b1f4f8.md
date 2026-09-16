### Title
Missing slippage protection in `PublicDelegation.stake`/`stakeFor` allows unprivileged stakers to receive fewer shares than expected - (File: contracts/bindings/publicdelegation/PublicDelegation.go, source Solidity not indexed)

### Summary
The `PublicDelegation` system contract exposes an ERC4626-style share-accounting model for staking KAIA (`convertToShares`, `convertToAssets`, `previewDeposit`, `previewRedeem`, `previewWithdraw`, `totalAssets`, `totalSupply`) together with payable entry points `stake()` and `stakeFor(address _recipient)` that convert deposited KAIA into shares. Neither entry point accepts a minimum-shares (or any slippage-bound) parameter, which is exactly the missing-slippage-control pattern described in the external report for `ERC4626DepositOnly.deposit`/`mint`.

### Finding Description
The generated bindings expose: [1](#0-0) [2](#0-1) 

and the vault's share-conversion view functions: [3](#0-2) [4](#0-3) 

together with the `Staked` event that records the resulting `shares` for the KAIA `assets` sent: [5](#0-4) 

`stake()`/`stakeFor()` are `payable` functions with no other input than value (and, for `stakeFor`, a recipient address) — there is no `minShares` argument. As in the reported `ERC4626DepositOnly.mint`/`deposit` case, any unprivileged caller (a staker/delegator sending KAIA to `PublicDelegation`) has the number of shares minted determined solely by `convertToShares(assets)` at execution time, computed from `totalAssets()`/`totalSupply()` at the moment the transaction is mined. Because this is a share-price vault backed by staking rewards (`reward()` is also exposed), the price-per-share can move between the time the staker submits the transaction and the time it is included (due to intervening reward accruals, other stakers' deposits/withdrawals, or transaction ordering), with no way for the caller to specify and enforce a minimum acceptable shares output.

Note: The Solidity source for `PublicDelegation` is not present in the currently indexed contracts directories (only the Go ABI bindings are indexed here), so the exact internal share-math/rounding cannot be directly quoted from source. This is a limitation of the current index rather than proof the bug does not exist; the ABI-level absence of a `minShares`/slippage parameter on `stake`/`stakeFor` is confirmed directly from the bindings above.

### Impact Explanation
A staker or fee-delegation counterparty calling `stake()`/`stakeFor()` directly (an EOA/unprivileged tx sender, exactly the actor class allowed by scope) can receive fewer `PublicDelegation` shares than expected for the KAIA sent if the exchange rate shifts between transaction submission and inclusion (e.g., due to reward distribution timing, another staker's concurrent stake/unstake, or MEV-style transaction ordering by the block proposer). This is a direct unauthorized-value-transfer-adjacent condition: value (shares representing a claim on staked assets + future rewards) can be minted at a worse rate than the user intended, and there is no on-chain mechanism to revert if that occurs — matching the "unauthorized value movement" bar via unexpected share dilution/slippage loss for the depositor.

### Likelihood Explanation
Likelihood is moderate: it requires either (a) natural reward-accrual timing between submission and inclusion or (b) an adversarial actor (e.g., a block proposer or another staker) intentionally sandwiching a `stake`/`stakeFor` call with actions that move the share price unfavorably before the victim's transaction lands. Given `PublicDelegation` is a core, publicly reachable staking/reward-distribution system contract used by ordinary stakers (in scope: "staking and reward distribution" and "system contracts"), and the call requires only a single submitted transaction with no special privilege, this is readily reachable by any public RPC caller.

### Recommendation
Add slippage-bounded variants of the deposit-style entry points, mirroring the `UlyssesRouter.addLiquidity` pattern cited in the original report: e.g. `stake(uint256 minShares)` / `stakeFor(address recipient, uint256 minShares)` that compute `shares = convertToShares(msg.value)` (or the actual minted shares) and `revert` if `shares < minShares`. Alternatively, expose a preview-based external check the same transaction can assert via `require`, or document/require use of a slippage-aware periphery router that computes `previewDeposit`/`convertToShares` and reverts atomically if the resulting shares fall below caller-supplied bounds.

### Proof of Concept
1. Staker Alice intends to convert 100 KAIA into `PublicDelegation` shares when the current exchange rate implies she should receive `S` shares (per `previewDeposit(100 KAIA)`).
2. Alice submits `stake()` (payable, 100 KAIA) with no `minShares` argument, since none exists in the ABI.
3. Before Alice's transaction is included, additional stakes/unstakes or timing of reward distribution shift `totalAssets()`/`totalSupply()`, so `convertToShares(100 KAIA)` now yields `S' < S`.
4. Alice's transaction still succeeds (there is no check against her expected minimum), and she is minted `S'` shares, receiving less value than expected, with no ability to have reverted the transaction on-chain. [1](#0-0) [5](#0-4)

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

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L836-851)
```go
// PreviewDeposit is a free data retrieval call binding the contract method 0xef8b30f7.
//
// Solidity: function previewDeposit(uint256 _assets) view returns(uint256)
func (_PublicDelegation *PublicDelegationCaller) PreviewDeposit(opts *bind.CallOpts, _assets *big.Int) (*big.Int, error) {
	var out []interface{}
	err := _PublicDelegation.contract.Call(opts, &out, "previewDeposit", _assets)

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L1283-1302)
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
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L1304-1322)
```go
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
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L3087-3093)
```go
// PublicDelegationStaked represents a Staked event raised by the PublicDelegation contract.
type PublicDelegationStaked struct {
	User   common.Address
	Assets *big.Int
	Shares *big.Int
	Raw    types.Log // Blockchain specific contextual infos
}
```

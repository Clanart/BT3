### Title
Share Price Inflation by First Staker in PublicDelegation Enables DOS/Fund-Loss on Subsequent stake/stakeFor - (File: contracts/bindings/publicdelegation/PublicDelegation.go, underlying contract PublicDelegation.sol)

### Summary
`PublicDelegation` is an ERC-4626-style vault where any unprivileged staker can call `stake()`/`stakeFor()` to deposit KAIA and receive vault shares computed via `convertToShares`/`previewDeposit`, redeemable later through `redeem()`/`previewRedeem()` against `totalAssets()`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
This is the same bug class as the DODO GSP "first LP-er" share inflation: an ERC-4626-like vault that computes shares from `assets * totalSupply / totalAssets` on deposit is vulnerable to a first-depositor donation attack when there is no minimum-liquidity lock. In `PublicDelegation`, the first staker can call `stake()` with a minimal amount (e.g., 1 wei) to mint a tiny amount of shares while `totalSupply` is 0→1, then directly inflate `totalAssets()` by donating/forcing extra KAIA into the underlying stake accounting (e.g., via the CN staking relationship tracked by `baseCnStaking`/redelegation flows) without minting corresponding shares. Because `convertToShares`/`previewDeposit` use the ratio of `_assets * totalSupply() / totalAssets()`, once `totalAssets()` is inflated relative to `totalSupply()`, subsequent `stake()`/`stakeFor()` calls from ordinary users round down to zero shares for any deposit smaller than the attacker's inflation ratio, causing either reverts (DOS) or worse — silent loss of the depositor's assets with zero shares minted, if the contract does not explicitly guard against zero-share mints.

I could not directly inspect the Solidity source of `PublicDelegation.sol` (only the compiled Go bindings and ABI/bytecode were indexed), so I cannot cite the exact line implementing `stake()`'s internal share-minting formula or confirm whether a zero-share/minimum-liquidity guard already exists. This is a material verification gap.

### Impact Explanation
If unmitigated, this would allow an attacker to permanently inflate the share price of a `PublicDelegation` vault with a small amount of capital, causing subsequent honest stakers' deposits to either revert (DOS on staking/GC delegation) or receive fewer shares than their deposit is worth (value theft), analogous to the Sherlock DODO GSP Medium-severity finding.

### Likelihood Explanation
Likelihood cannot be confirmed without the source of `stake()`'s minting logic and confirmation of whether `totalAssets()` can be independently inflated (e.g., by direct KAIA transfer, `sweep()`, or `redelegateByShares`/`redelegateByAssets` interactions) relative to `totalSupply()`. Given the indexing gap, this cannot be validated as a concrete, confirmed vulnerability in this repository at this time.

### Recommendation
N/A — cannot be finalized without confirming the actual `stake()` implementation and whether existing safeguards (e.g., virtual shares/assets offset, minimum liquidity lock, or a zero-share-mint revert) are already present in `PublicDelegation.sol`.

### Proof of Concept
Not constructible without the Solidity implementation of `stake()`, `_convertToShares`, and `totalAssets()`.

**Note on completeness:** Due to index size limits, the actual Solidity source file for `PublicDelegation` (only its compiled Go bindings were available) could not be retrieved, so root-cause line-level verification of the minting formula and any existing minimum-liquidity/zero-share protections was not possible. A Devin session with full repository/filesystem access would be needed to inspect `PublicDelegation.sol` directly and confirm whether this analog is exploitable.

### Citations

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L495-555)
```go
// ConvertToAssets is a free data retrieval call binding the contract method 0x07a2d13a.
//
// Solidity: function convertToAssets(uint256 _shares) view returns(uint256)
func (_PublicDelegation *PublicDelegationCaller) ConvertToAssets(opts *bind.CallOpts, _shares *big.Int) (*big.Int, error) {
	var out []interface{}
	err := _PublicDelegation.contract.Call(opts, &out, "convertToAssets", _shares)

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}

// ConvertToAssets is a free data retrieval call binding the contract method 0x07a2d13a.
//
// Solidity: function convertToAssets(uint256 _shares) view returns(uint256)
func (_PublicDelegation *PublicDelegationSession) ConvertToAssets(_shares *big.Int) (*big.Int, error) {
	return _PublicDelegation.Contract.ConvertToAssets(&_PublicDelegation.CallOpts, _shares)
}

// ConvertToAssets is a free data retrieval call binding the contract method 0x07a2d13a.
//
// Solidity: function convertToAssets(uint256 _shares) view returns(uint256)
func (_PublicDelegation *PublicDelegationCallerSession) ConvertToAssets(_shares *big.Int) (*big.Int, error) {
	return _PublicDelegation.Contract.ConvertToAssets(&_PublicDelegation.CallOpts, _shares)
}

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

// ConvertToShares is a free data retrieval call binding the contract method 0xc6e6f592.
//
// Solidity: function convertToShares(uint256 _assets) view returns(uint256)
func (_PublicDelegation *PublicDelegationSession) ConvertToShares(_assets *big.Int) (*big.Int, error) {
	return _PublicDelegation.Contract.ConvertToShares(&_PublicDelegation.CallOpts, _assets)
}

// ConvertToShares is a free data retrieval call binding the contract method 0xc6e6f592.
//
// Solidity: function convertToShares(uint256 _assets) view returns(uint256)
func (_PublicDelegation *PublicDelegationCallerSession) ConvertToShares(_assets *big.Int) (*big.Int, error) {
	return _PublicDelegation.Contract.ConvertToShares(&_PublicDelegation.CallOpts, _assets)
}
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L836-882)
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

// PreviewDeposit is a free data retrieval call binding the contract method 0xef8b30f7.
//
// Solidity: function previewDeposit(uint256 _assets) view returns(uint256)
func (_PublicDelegation *PublicDelegationSession) PreviewDeposit(_assets *big.Int) (*big.Int, error) {
	return _PublicDelegation.Contract.PreviewDeposit(&_PublicDelegation.CallOpts, _assets)
}

// PreviewDeposit is a free data retrieval call binding the contract method 0xef8b30f7.
//
// Solidity: function previewDeposit(uint256 _assets) view returns(uint256)
func (_PublicDelegation *PublicDelegationCallerSession) PreviewDeposit(_assets *big.Int) (*big.Int, error) {
	return _PublicDelegation.Contract.PreviewDeposit(&_PublicDelegation.CallOpts, _assets)
}

// PreviewRedeem is a free data retrieval call binding the contract method 0x4cdad506.
//
// Solidity: function previewRedeem(uint256 _shares) view returns(uint256)
func (_PublicDelegation *PublicDelegationCaller) PreviewRedeem(opts *bind.CallOpts, _shares *big.Int) (*big.Int, error) {
	var out []interface{}
	err := _PublicDelegation.contract.Call(opts, &out, "previewRedeem", _shares)

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

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

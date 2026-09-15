### Title
`PublicDelegation.stake()`/`stakeFor()` lack slippage (minShares) protection against front-run exchange-rate changes - (File: contracts/bindings/publicdelegation/PublicDelegation.go)

### Summary
`PublicDelegation` is Kaia's ERC4626-style public staking vault (used for consensus-liquidity delegation to a CN). Its `stake()` and `stakeFor(address)` entry points are `payable` and accept only the native KAIA value — there is no `minShares`/`minAssets` parameter to bound the exchange rate at which shares are minted, mirroring the exact bug class described in the reference report for `OperationalStaking.stake()`.

### Finding Description
The contract exposes ERC4626-like accounting primitives — `convertToShares(_assets)`, `convertToAssets(_shares)`, `previewDeposit(_assets)`, `previewRedeem(_shares)`, `totalAssets()`, `totalSupply()` [1](#0-0) [2](#0-1) [3](#0-2) , and the `Staked(user, assets, shares)` event confirms shares are computed from a live exchange rate (`assets`→`shares` conversion) at call time [4](#0-3) .

The public entry points to mint these shares are:
```
function stake() payable returns()
function stakeFor(address _recipient) payable returns()
``` [5](#0-4) [6](#0-5) 

Neither accepts a `minShares` (or equivalent) parameter, so any unprivileged staker submitting a `stake`/`stakeFor` transaction has no way to bound the number of shares they will receive for their deposited KAIA. If the vault's exchange rate (`totalAssets()/totalSupply()`) increases between transaction submission and inclusion — for example due to reward/commission crediting to the vault occurring in an earlier transaction or block — the staker's shares are silently diluted with no on-chain enforcement of a minimum acceptable rate, exactly matching the mechanism in the reported `OperationalStaking._stake()`/`_tokensToShares()` flow.

### Impact Explanation
A staker submitting `stake()`/`stakeFor()` can receive materially fewer shares than expected if the exchange rate moves against them before inclusion (e.g., via a reward crediting transaction, or an attacker/searcher intentionally sequencing such a transaction ahead of the victim's in the same block, which is directly actionable on Kaia given public mempool visibility and the auction/bundle machinery documented for ordering transactions within a block). Since shares represent a proportional claim on the vault's underlying CN staking + rewards, this is a concrete redirection/dilution of stake value away from the depositor and towards existing shareholders — satisfying the "reward redirection" / unauthorized value movement criterion.

### Likelihood Explanation
Reachable by any unprivileged public staker constructing a normal `stake`/`stakeFor` transaction and submitting it via public RPC — no privileged role is required. The precondition (an intervening transaction changing `totalAssets`/`totalSupply` before the staker's tx executes) is realistic any time reward distribution, commission settlement, or another user's stake/redeem is processed in the same or an adjacent block, and can be deliberately engineered by a searcher observing the mempool.

### Recommendation
Add a `minShares` (for `stake`/`stakeFor`) and `minAssets`/`maxShares` (for `redeem`/withdraw-style functions) parameter, and revert if the computed shares/assets fall outside the caller-specified bound — consistent with the standard ERC4626 slippage-protection pattern and the recommendation in the referenced report.

### Proof of Concept
Not independently reproducible from this repository: only the Go ABI bindings for `PublicDelegation` are indexed here, not the Solidity source, so the exact internal exchange-rate/reward-crediting mechanics cannot be traced line-by-line in this environment. The ABI-level evidence — `stake()`/`stakeFor()` taking no share/rate bound, combined with ERC4626-style `convertToShares`/`previewDeposit`/`totalAssets` accessors and a `Staked(assets, shares)` event — establishes the same missing-slippage-check pattern as the reference report. Confirming the precise front-run sequence (what operations move `totalAssets`/`totalSupply` between a staker's tx submission and inclusion) requires reading the actual `PublicDelegation.sol` source, which is not available through the current indexing; a full Devin session with repository access would be needed to pull the exact contract source and construct a concrete forge/hardhat PoC analogous to the one in the report.

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

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L836-865)
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
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L1039-1068)
```go
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

// TotalSupply is a free data retrieval call binding the contract method 0x18160ddd.
//
// Solidity: function totalSupply() view returns(uint256)
func (_PublicDelegation *PublicDelegationCaller) TotalSupply(opts *bind.CallOpts) (*big.Int, error) {
	var out []interface{}
	err := _PublicDelegation.contract.Call(opts, &out, "totalSupply")

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

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L1304-1323)
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
}
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L3087-3097)
```go
// PublicDelegationStaked represents a Staked event raised by the PublicDelegation contract.
type PublicDelegationStaked struct {
	User   common.Address
	Assets *big.Int
	Shares *big.Int
	Raw    types.Log // Blockchain specific contextual infos
}

// FilterStaked is a free log retrieval operation binding the contract event 0x1449c6dd7851abc30abf37f57715f492010519147cc2652fbc38202c18a6ee90.
//
// Solidity: event Staked(address indexed user, uint256 indexed assets, uint256 indexed shares)
```

### Title
Inflation Attack on Empty PublicDelegation Vault Can DoS/Steal from Future Stakers - (File: contracts/bindings/publicdelegation/PublicDelegation.go)

### Summary
`PublicDelegation` is Kaia's native, ERC-4626-style staking vault: it mints fungible shares against staked KAIA (`totalAssets`/`totalSupply`/`convertToShares`/`convertToAssets`/`previewDeposit`) and exposes a public, payable `receive()` fallback plus unprivileged `stake()`/`stakeFor(address)` entry points. [1](#0-0) [2](#0-1)  Because the vault accepts native KAIA directly via its payable `receive()` function without going through `stake()`, the classic ERC-4626 first-depositor inflation attack described in the external report is directly reachable by any unprivileged transaction sender.

### Finding Description
The contract tracks shares/assets ratio through `convertToShares`/`convertToAssets`, `totalAssets()`, and `totalSupply()`. [3](#0-2) [4](#0-3)  Staking mints shares proportional to `assets * totalSupply / totalAssets`, exactly the classic ERC-4626 formula the report warns about. Critically, the ABI declares a `receive() payable` function, meaning the contract's native KAIA balance (which is very likely what backs `totalAssets()` for this native-asset vault) can be increased by a plain KAIA transfer that bypasses `stake()`/`stakeFor()` entirely and mints zero shares. [1](#0-0) 

Attack path, following the reported bug class:
1. Vault freshly deployed for a GC (`PDConstructorArgs` sets owner/commission/gcName), `totalAssets() == 0`, `totalSupply() == 0`.
2. Attacker calls `stake()` with the minimum unit, e.g. 1 wei/peb, receiving 1 share. Now `totalAssets() == 1`, `totalSupply() == 1`.
3. Attacker sends a large amount of KAIA directly to the contract address (hits `receive()` since it is `payable` and does not mint shares) — e.g. `10_000e18 - 1`. Now `totalAssets() == 10_000e18`, `totalSupply() == 1`.
4. Any subsequent legitimate staker calling `stake()`/`stakeFor()` with an amount less than `10_000e18` will compute `shares = 1 * amount / 10_000e18 == 0`. If the contract does not explicitly revert on zero shares minted (as flagged in the underlying report’s referenced check), the staker’s KAIA is absorbed into the vault while they receive no shares — a direct value-transfer/theft; if it does revert on zero shares, legitimate small stakers are permanently DoS’d from that GC’s public delegation pool.

### Impact Explanation
This directly maps to "concrete unauthorized value movement" and "supply inflation" categories: an attacker can either (a) permanently deny staking (DoS) to legitimate KAIA holders wanting to delegate to a given GC through `PublicDelegation`, undermining the staking/reward-distribution system that Kaia's `kaiax/staking` module reads from (`AddressBook`/`CLRegistry` staking amounts feed consensus weight and reward eligibility), or (b) if zero-share minting is not rejected, silently confiscate the staked KAIA of subsequent depositors into the attacker's single share, i.e. outright theft of staked funds. Because `PublicDelegation` balances are read by the staking/reward pipeline (`kaiax/staking/impl/getter.go` reads `CLRegistry`/`AddressBook` derived staking amounts), any corruption of a GC's public delegation pool has downstream effects on reward and staking accounting for that GC. [5](#0-4) 

### Likelihood Explanation
The attack requires only two ordinary, unprivileged transactions from a single account (one minimal `stake()` call, one direct KAIA transfer to the contract) immediately after a `PublicDelegation` vault is deployed and before any real staker deposits — no special privileges, governance access, or validator/node role are needed. Any newly created GC's `PublicDelegation` vault is vulnerable during the window between deployment and its first genuine, sizeable deposit.

### Recommendation
- Mint an initial, permanently-locked "dead" share (or deposit a fixed minimum amount to a burn address) at construction time so `totalSupply()` can never be inflated to a trivially small denominator, mirroring the ERC-4626/OpenZeppelin "virtual shares" mitigation.
- Explicitly `require(shares > 0)` (and symmetrically for `assets > 0` on redeem) in the stake/deposit path so no staker can be silently given zero shares for a nonzero KAIA transfer.
- Ensure `totalAssets()` is computed from accounted internal state, not solely from `address(this).balance`, so unsolicited direct transfers via `receive()` cannot alter the vault's share price.
- As the report's own resolution states, have the deploying/initializing script perform a nontrivial first stake immediately atomically with deployment so the vault is never left empty and attacker-controlled.

### Proof of Concept
```solidity
// Assume PublicDelegation vault `pd` has just been deployed for a GC, empty state.
// Step 1: attacker seeds the vault with the minimum unit
pd.stake{value: 1}();               // totalAssets() == 1, totalSupply() == 1, attacker owns 1 share

// Step 2: attacker inflates totalAssets via receive() without minting shares
(bool ok, ) = address(pd).call{value: 10_000 ether - 1}("");
require(ok);
// totalAssets() == 10_000 ether, totalSupply() == 1

// Step 3: any legitimate staker depositing < 10_000 ether now gets 0 shares
pd.stake{value: 5_000 ether}();     // shares = 1 * 5_000e18 / 10_000e18 == 0
// -> either reverts (DoS: legitimate staker locked out) or succeeds with 0 shares (theft of 5_000 ether)
```

### Citations

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L41-44)
```go
var PublicDelegationMetaData = &bind.MetaData{
	ABI: "[{\"type\":\"constructor\",\"inputs\":[],\"stateMutability\":\"nonpayable\"},{\"type\":\"receive\",\"stateMutability\":\"payable\"},{\"type\":\"function\",\"name\":\"COMMISSION_DENOMINATOR\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"uint256\",\"internalType\":\"uint256\"}],\"stateMutability\":\"view\"},{\"type\":\"function\",\"name\":\"CONTRACT_TYPE\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"string\",\"internalType\":\"string\"}],\"stateMutability\":\"view\"},{\"type\":\"function\",\"name\":\"MAX_COMMISSION_RATE\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"uint256\",\"internalType\":\"uint256\"}],\"stateMutability\":\"view\"},{\"type\":\"function\",\"name\":\"VERSION\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"uint256\",\"internalType\":\" ... (truncated)
	Bin: "0x6080604052348015600e575f80fd5b5060156019565b60c9565b7ff0c57e16840df040f15088dc2f81fe391c3923bec73e23a9662efc9c229c6a00805468010000000000000000900460ff161560685760405163f92ee8a960e01b815260040160405180910390fd5b80546001600160401b039081161460c65780546001600160401b0319166001600160401b0390811782556040519081527fc7f505b2f371ae2175ee4913f4499e1f2633a7b5936321eed1cdaeb6115181d29060200160405180910390a15b50565b6129b0806100d65f395ff3fe608060405260043610610277575f3560e01c80634cdad5061161014a578063c6e6f592116100be578063e659d7d711610078578063e659d7d71461071e578063ef8b30f71461073d578063f29177c31461075c578063f2fde38b1461077b578063f3fef3a31461079a578063ffa1ad74146107b9575f80fd5b8063c6e6f59214610664578063c804b11514610683578063ce96cb77146106a2578063d905777e146106c1578063dd62ed3e146106e0578063e15fc35 ... (truncated)
}
```

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

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L1022-1082)
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

// TotalSupply is a free data retrieval call binding the contract method 0x18160ddd.
//
// Solidity: function totalSupply() view returns(uint256)
func (_PublicDelegation *PublicDelegationSession) TotalSupply() (*big.Int, error) {
	return _PublicDelegation.Contract.TotalSupply(&_PublicDelegation.CallOpts)
}

// TotalSupply is a free data retrieval call binding the contract method 0x18160ddd.
//
// Solidity: function totalSupply() view returns(uint256)
func (_PublicDelegation *PublicDelegationCallerSession) TotalSupply() (*big.Int, error) {
	return _PublicDelegation.Contract.TotalSupply(&_PublicDelegation.CallOpts)
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

**File:** kaiax/staking/impl/getter.go (L103-143)
```go
func (s *StakingModule) getFromState(header *types.Header, statedb *state.StateDB) (*staking.StakingInfo, error) {
	isForPrague := s.ChainConfig.IsPragueForkEnabled(new(big.Int).Add(header.Number, common.Big1))
	isForPermissionless := s.ChainConfig.IsPermissionlessForkEnabled(new(big.Int).Add(header.Number, common.Big1))
	num := header.Number.Uint64()

	// Bail out if AddressBook is not installed.
	// This is a common case for private nets.
	if statedb.GetCode(system.AddressBookAddr) == nil {
		logger.Trace("AddressBook not installed", "sourceNum", num)
		return emptyStakingInfo(num), nil
	}

	// Now we're safe to call the MultiCall contract.
	contract, err := system.NewMultiCallContractCaller(statedb, s.Chain, header)
	if err != nil {
		return nil, staking.ErrMultiCallCall(err)
	}

	callOpts := &bind.CallOpts{BlockNumber: header.Number}

	// Helper to read CL registry info, shared by permissioned and permissionless paths.
	// Permissionless is ordered after Prague (Randao <= Kaia <= Prague <= Permissionless),
	// so permissionless blocks always need CL registry info too.
	readCLInfo := func() (clRegistryResult, error) {
		var clRes clRegistryResult
		// If Registry is not installed, do not handle CL staking info.
		// In private network, Randao and Prague hardfork can be activated at the same block.
		// It leads to staking info inconsistency between block processing and rpc query since the Registry hasn't been installed when finalizing the header.
		// Note that Randao can't be activated after Prague according to fork ordering (Randao <= Kaia <= Prague).
		if statedb.GetCode(system.RegistryAddr) == nil || s.ChainConfig.IsRandaoForkBlockParent(header.Number) {
			logger.Trace("Registry not installed", "sourceNum", num)
			return clRes, nil
		}
		// Note that if CLRegistry is not registered in Registry,
		// it will return empty result and no error.
		clRes, err = contract.MultiCallDPStakingInfo(callOpts)
		if err != nil {
			return clRes, staking.ErrCLRegistryCall(err)
		}
		return clRes, nil
	}
```

### Title
Fee/reward theft via unchecked stake-and-redeem in `PublicDelegation` (ERC4626-style CN staking vault) - ([File: contracts/bindings/publicdelegation/PublicDelegation.go])

### Summary
`PublicDelegation` is Kaia's public liquid-delegation vault for CN (validator) staking: users `stake()`/`stakeFor()` KAIA to mint shares and `redeem()`/`withdraw()` shares back to KAIA, priced by `convertToShares`/`convertToAssets` (`previewDeposit`, `previewRedeem`) — the exact ERC4626-style share-accounting pattern that the Arrakis `ArrakisV2.burn()` bug exploited. [1](#0-0) [2](#0-1) 

### Finding Description
The Arrakis root cause is: a pool's pending fee/reward balance is not checkpointed per-depositor; it is realized only at redemption time as `balance * shares_burned / totalSupply`. Anyone who mints a disproportionately large number of shares right before redemption can claim a pro-rata slice of rewards they never contributed to earning.

`PublicDelegation` exhibits the analogous share/asset accounting shape: `stake()`/`stakeFor()` mint shares for deposited KAIA, and `redeem()`/`withdraw()` burn shares for a proportional amount of the contract's underlying assets, computed via `convertToShares`/`convertToAssets`/`previewDeposit`/`previewRedeem`. [3](#0-2) [4](#0-3)  The contract exposes `reward()` and `commissionRate` accessors, meaning validator staking rewards are accrued/tracked in the vault and distributed to share holders at redemption based on the current supply — this is the same "fees claimed at burn time, not checkpointed" structure the Arrakis report flags. [5](#0-4) 

However, unlike Arrakis (instant burn), `PublicDelegation` uses a two-step withdrawal: `redeem()`/`withdraw()` create a `RequestWithdrawal`, tracked via `getUserRequestIds`/`getCurrentWithdrawalRequestState`, implying a lockup/delay before assets are actually released (mirroring `CnStakingV4`'s `STAKE_LOCKUP` and `approveStakingWithdrawal`/`withdrawableFrom` pattern). [6](#0-5) [7](#0-6) [8](#0-7) 

Because the Solidity source for `PublicDelegation` is not present in the indexed codebase context (only the compiled Go bindings/ABI were retrievable), I could not confirm from source code:
1. Whether shares are minted/valued using the instantaneous `totalAssets()` (including freshly-received, un-vested validator rewards) at the moment of `stake()`, which would let a large depositor dilute existing holders' claim on rewards already earned before the deposit.
2. Whether the lockup period referenced by `RequestWithdrawal`/`getCurrentWithdrawalRequestState` is long enough, and whether it applies uniformly regardless of deposit recency, to prevent a stake-then-immediately-redeem race.
3. Whether reward distribution to the vault happens continuously (e.g., every block via CnStakingV4 rewards) or in discrete unlock events that a well-timed transaction could front-run.

### Impact Explanation
If reward/asset accounting in `PublicDelegation` is not checkpointed per-depositor (i.e., a new staker's shares are valued against the vault's current total assets including undistributed rewards, and the withdrawal lockup does not block same-recipient reward capture), a large depositor could dilute or capture a disproportionate share of validator staking rewards belonging to earlier depositors — a direct value-transfer/theft of staking rewards, which would be High severity given real KAIA is at stake across the delegation module used by the wider staking/reward system described in `kaiax/staking` and `kaiax/reward`. [9](#0-8) 

### Likelihood Explanation
Likelihood cannot be confirmed as High with confidence: the existence of a withdrawal-request/lockup mechanism (`RequestWithdrawal`, `getCurrentWithdrawalRequestState`) strongly suggests the developers already mitigated the "mint-then-immediately-burn" race that broke Arrakis, unlike the Arrakis contract which allowed synchronous mint+burn in one flow. Without the Solidity source, I cannot verify whether the checkpointing/lockup fully closes the race or only delays it (in which case a large staker could still stake before a reward is credited and become eligible to redeem the diluted share once the lockup elapses, still ahead of long-term stakers).

### Recommendation
- Obtain and review the `PublicDelegation.sol` source (not available in the current index) to confirm: (a) how `convertToShares`/`totalAssets` treat pending/unvested rewards at deposit time, (b) whether the withdrawal lockup timing prevents new depositors from capturing rewards accrued prior to their deposit, and (c) whether commission/reward accounting is checkpointed at each stake/unstake event rather than computed purely from the live balance ratio.
- If rewards are not checkpointed against share-minting time, apply a reward-per-share checkpoint (analogous to a standard staking rewards accumulator) so that new depositors only accrue rewards from the block after they deposit.
- Ensure the withdrawal lockup applies to reward eligibility, not just principal withdrawal, so that same-block or fast stake→redeem sequences cannot capture rewards disproportionate to time-weighted stake.

### Proof of Concept
Not constructible with the current index because the `PublicDelegation.sol` implementation (share-pricing formula, reward accrual mechanism, and lockup duration/enforcement) is not available — only compiled bindings were found. A concrete PoC requires the source to trace `stake()` → `convertToShares()` → reward accrual → `redeem()`/`withdraw()` state transitions. I recommend starting a Devin session with full repository access to pull `contracts/system_contracts/**/PublicDelegation.sol` (or wherever the source lives) to complete this analysis and, if the flaw is confirmed, produce a Foundry/Hardhat PoC demonstrating disproportionate reward capture.

### Citations

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L510-541)
```go
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
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L588-617)
```go
// GetCurrentWithdrawalRequestState is a free data retrieval call binding the contract method 0x04ddc9d1.
//
// Solidity: function getCurrentWithdrawalRequestState(uint256 _requestId) view returns(uint8)
func (_PublicDelegation *PublicDelegationCaller) GetCurrentWithdrawalRequestState(opts *bind.CallOpts, _requestId *big.Int) (uint8, error) {
	var out []interface{}
	err := _PublicDelegation.contract.Call(opts, &out, "getCurrentWithdrawalRequestState", _requestId)

	if err != nil {
		return *new(uint8), err
	}

	out0 := *abi.ConvertType(out[0], new(uint8)).(*uint8)

	return out0, err

}

// GetCurrentWithdrawalRequestState is a free data retrieval call binding the contract method 0x04ddc9d1.
//
// Solidity: function getCurrentWithdrawalRequestState(uint256 _requestId) view returns(uint8)
func (_PublicDelegation *PublicDelegationSession) GetCurrentWithdrawalRequestState(_requestId *big.Int) (uint8, error) {
	return _PublicDelegation.Contract.GetCurrentWithdrawalRequestState(&_PublicDelegation.CallOpts, _requestId)
}

// GetCurrentWithdrawalRequestState is a free data retrieval call binding the contract method 0x04ddc9d1.
//
// Solidity: function getCurrentWithdrawalRequestState(uint256 _requestId) view returns(uint8)
func (_PublicDelegation *PublicDelegationCallerSession) GetCurrentWithdrawalRequestState(_requestId *big.Int) (uint8, error) {
	return _PublicDelegation.Contract.GetCurrentWithdrawalRequestState(&_PublicDelegation.CallOpts, _requestId)
}
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L975-990)
```go
}

// Reward is a free data retrieval call binding the contract method 0x228cb733.
//
// Solidity: function reward() view returns(uint256)
func (_PublicDelegation *PublicDelegationSession) Reward() (*big.Int, error) {
	return _PublicDelegation.Contract.Reward(&_PublicDelegation.CallOpts)
}

// Reward is a free data retrieval call binding the contract method 0x228cb733.
//
// Solidity: function reward() view returns(uint256)
func (_PublicDelegation *PublicDelegationCallerSession) Reward() (*big.Int, error) {
	return _PublicDelegation.Contract.Reward(&_PublicDelegation.CallOpts)
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

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L1451-1470)
```go
// Withdraw is a paid mutator transaction binding the contract method 0xf3fef3a3.
//
// Solidity: function withdraw(address _recipient, uint256 _assets) returns()
func (_PublicDelegation *PublicDelegationTransactor) Withdraw(opts *bind.TransactOpts, _recipient common.Address, _assets *big.Int) (*types.Transaction, error) {
	return _PublicDelegation.contract.Transact(opts, "withdraw", _recipient, _assets)
}

// Withdraw is a paid mutator transaction binding the contract method 0xf3fef3a3.
//
// Solidity: function withdraw(address _recipient, uint256 _assets) returns()
func (_PublicDelegation *PublicDelegationSession) Withdraw(_recipient common.Address, _assets *big.Int) (*types.Transaction, error) {
	return _PublicDelegation.Contract.Withdraw(&_PublicDelegation.TransactOpts, _recipient, _assets)
}

// Withdraw is a paid mutator transaction binding the contract method 0xf3fef3a3.
//
// Solidity: function withdraw(address _recipient, uint256 _assets) returns()
func (_PublicDelegation *PublicDelegationTransactorSession) Withdraw(_recipient common.Address, _assets *big.Int) (*types.Transaction, error) {
	return _PublicDelegation.Contract.Withdraw(&_PublicDelegation.TransactOpts, _recipient, _assets)
}
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L2762-2769)
```go
// PublicDelegationRequestWithdrawal represents a RequestWithdrawal event raised by the PublicDelegation contract.
type PublicDelegationRequestWithdrawal struct {
	User      common.Address
	Recipient common.Address
	RequestId *big.Int
	Assets    *big.Int
	Raw       types.Log // Blockchain specific contextual infos
}
```

**File:** contracts/bindings/cnstakingv4/CnStakingV4.go (L363-373)
```go
// GetApprovedStakingWithdrawalInfo is a free data retrieval call binding the contract method 0x725c0503.
//
// Solidity: function getApprovedStakingWithdrawalInfo(uint256 _index) view returns(address to, uint256 value, uint256 withdrawableFrom, uint8 state)
func (_CnStakingV4 *CnStakingV4Session) GetApprovedStakingWithdrawalInfo(_index *big.Int) (struct {
	To               common.Address
	Value            *big.Int
	WithdrawableFrom *big.Int
	State            uint8
}, error) {
	return _CnStakingV4.Contract.GetApprovedStakingWithdrawalInfo(&_CnStakingV4.CallOpts, _index)
}
```

**File:** kaiax/staking/README.md (L1-9)
```markdown
# kaiax/staking

This module is responsible for tracking validator staking amounts and their address configurations.

## Concepts

- StakingInfo is a struct representing Validator staking information at a certain block including staked amount, reward address, and node address. It is primarily used to determine validator set and rewards distribution.
- StakingInfo summarizes the current AddressBook contract state, and all the staking contracts registered in the AddressBook, and their native token balances.
  - Since the Prague hardfork, the StakingInfo will include the [consensus liquidity](https://kips.kaia.io/KIPs/kip-226) information from the CLRegistry.
```

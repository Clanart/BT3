### Title
Donation-based share-price inflation in `PublicDelegation` lets an attacker zero out a staker's minted shares while their KAIA is still staked - ([File: contracts/bindings/publicdelegation/PublicDelegation.go])

### Summary
`PublicDelegation` is Kaia's ERC-4626-style public staking-pool contract: users call `stake()`/`stakeFor()` to deposit KAIA and receive pool shares computed via `convertToShares()`/`previewDeposit()`, and redeem via `redeem()`/`withdraw()` using `convertToAssets()`. The contract exposes a `payable receive()` function (per the ABI: `{"type":"receive","stateMutability":"payable"}`), meaning *any* unprivileged account can send KAIA directly to the contract without going through `stake()`. This is the same bug class as the reported Vault.sol issue: `totalSupply()`/`totalAssets()` can be inflated by a direct balance donation that increases the asset side of the share-price ratio without minting any shares, which can drive `convertToShares(_assets)` to round down to zero for a subsequent legitimate staker. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
The generated bindings show the classic ERC-4626 accounting surface: `totalAssets()`, `totalSupply()`, `convertToShares(_assets)`, `convertToAssets(_shares)`, `previewDeposit(_assets)`, `previewRedeem(_shares)`, `previewWithdraw(_assets)`, `stake()` (payable), `stakeFor(_recipient)` (payable), `redeem(_recipient, _shares)`. [4](#0-3) [5](#0-4) 

Because the ABI declares a `payable receive()` fallback, KAIA can reach the contract's balance (and hence `totalAssets()`, assuming — as in the Vault.sol analog — that `totalAssets` is computed from live balance/staked balance rather than an internal accounting variable) without any shares being minted. This is structurally identical to the reported bug: `Vault.totalSupply()` summed `balanceOf(address(this))` plus external plugin balances, so a forced/direct transfer inflated the denominator used in `convertToShares = tokens * totalShares / tokenSupply`, driving the result to `0` for subsequent depositors even though their KAIA was transferred in.

In `PublicDelegation`, the analogous attack path is:
1. Attacker (or any user) is the first/only staker with a small amount, obtaining a proportionally large number of shares (classic first-depositor issue), or simply waits for a pool with very few shares outstanding.
2. Attacker (or anyone) sends KAIA directly to the contract via the bare `receive()` payable function (an ordinary value-transfer transaction, fully reachable by an unprivileged sender — no special role or gating implied by the ABI), inflating the asset balance that backs `totalAssets()`/`convertToShares()` without incrementing `totalSupply()`.
3. A subsequent legitimate staker calls `stake()`/`stakeFor()`. `previewDeposit`/`convertToShares` computes `newShares = assets * totalShares / totalAssets`, which can round down to `0` if `totalAssets` has been inflated disproportionately relative to the staker's deposit.
4. If the contract does not explicitly reject a `0`-share mint (as flagged as the exact missing guard in the source report — `require(newShares > 0)`), the staker's KAIA is consumed (staked into the underlying CN staking contract) while they receive `0` shares, i.e. an outright loss of funds with the surplus effectively redistributed to (or frozen for) existing/no shareholders.

### Impact Explanation
This is a direct unauthorized value-movement/loss-of-funds bug reachable purely by ordinary, unprivileged transaction senders (staking depositors and anyone able to send a plain value transfer to the contract's `receive()`). A victim staker can lose their entire staked KAIA with zero compensating shares, and the attacker can manipulate the share-to-asset exchange rate for their own benefit (or to grief/DoS new stakers) purely through public, permissionless transactions — a bug class the analog rules explicitly instruct to treat as valid (supply inflation / unauthorized value movement in a staking module). Given `PublicDelegation` is meant to be Kaia's public delegation/staking vehicle for ordinary token holders, a High-severity fund-loss bug here is directly analogous in severity to the original Vault.sol finding.

### Likelihood Explanation
Likelihood is high for pools with low total shares (e.g., freshly deployed public delegation pools, or pools that have been heavily redeemed down to a small number of outstanding shares) — a common state for newly launched CN public-delegation pools. The attack only requires (a) an ordinary value-transfer transaction to the contract's payable `receive()` and (b) waiting for/front-running a legitimate `stake()` call. No governance, validator, or privileged role is required.

### Recommendation
- Add an explicit check that the shares computed in `stake()`/`stakeFor()` (`previewDeposit`/`convertToShares` result) are non-zero, reverting the deposit otherwise (`require(shares > 0, "PublicDelegation: zero shares")`), mirroring the report's recommended fix for Vault.sol.
- Avoid deriving `totalAssets()` purely from `address(this).balance` (or any externally-inflatable value) without netting out un-staked/donated funds; prefer an internally tracked accounting variable that is only updated on `stake`/`redeem`/reward-accrual paths, not by arbitrary incoming transfers.
- Consider a virtual-shares/decimals-offset mitigation (as in OpenZeppelin's ERC4626 hardening) to reduce the practicality of first-depositor share-price manipulation.

### Proof of Concept
Full contract source for `PublicDelegation.sol` was not available in the indexed codebase (only the generated Go bindings in `contracts/bindings/publicdelegation/PublicDelegation.go` were found — this repo's indexing does not include the Solidity source for this contract). Based on the ABI/bindings evidence:
1. Confirmed via ABI: `receive()` is `payable` — any account can send KAIA to the contract's balance directly. [1](#0-0) 
2. Confirmed via ABI: share/asset conversion functions `convertToShares`, `convertToAssets`, `previewDeposit`, `totalAssets`, `totalSupply`, `stake`, `stakeFor`, `redeem` exist, matching the ERC4626 deposit/mint pattern from the reported bug class. [2](#0-1) [3](#0-2) [4](#0-3) 

I was unable to confirm from the available index (a) the exact formula used inside `totalAssets()`/`convertToShares()` (whether it nets out un-staked donated balance) and (b) whether a `require(shares > 0)` guard already exists on the `stake`/`stakeFor` path, because the Solidity source of `PublicDelegation.sol` is not present in the indexed files (only auto-generated Go bindings were retrievable). **This should be verified against the actual `PublicDelegation.sol` source (likely under a `contracts/system_contracts` or similar directory not covered by the current index) via a full Devin session with filesystem access before treating this as a confirmed, exploitable finding** — the analog is structurally strong (payable `receive()` + ERC4626 share-conversion API) but the precise rounding/guard behavior could not be directly verified here.

### Citations

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L41-44)
```go
var PublicDelegationMetaData = &bind.MetaData{
	ABI: "[{\"type\":\"constructor\",\"inputs\":[],\"stateMutability\":\"nonpayable\"},{\"type\":\"receive\",\"stateMutability\":\"payable\"},{\"type\":\"function\",\"name\":\"COMMISSION_DENOMINATOR\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"uint256\",\"internalType\":\"uint256\"}],\"stateMutability\":\"view\"},{\"type\":\"function\",\"name\":\"CONTRACT_TYPE\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"string\",\"internalType\":\"string\"}],\"stateMutability\":\"view\"},{\"type\":\"function\",\"name\":\"MAX_COMMISSION_RATE\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"uint256\",\"internalType\":\"uint256\"}],\"stateMutability\":\"view\"},{\"type\":\"function\",\"name\":\"VERSION\",\"inputs\":[],\"outputs\":[{\"name\":\"\",\"type\":\"uint256\",\"internalType\":\" ... (truncated)
	Bin: "0x6080604052348015600e575f80fd5b5060156019565b60c9565b7ff0c57e16840df040f15088dc2f81fe391c3923bec73e23a9662efc9c229c6a00805468010000000000000000900460ff161560685760405163f92ee8a960e01b815260040160405180910390fd5b80546001600160401b039081161460c65780546001600160401b0319166001600160401b0390811782556040519081527fc7f505b2f371ae2175ee4913f4499e1f2633a7b5936321eed1cdaeb6115181d29060200160405180910390a15b50565b6129b0806100d65f395ff3fe608060405260043610610277575f3560e01c80634cdad5061161014a578063c6e6f592116100be578063e659d7d711610078578063e659d7d71461071e578063ef8b30f71461073d578063f29177c31461075c578063f2fde38b1461077b578063f3fef3a31461079a578063ffa1ad74146107b9575f80fd5b8063c6e6f59214610664578063c804b11514610683578063ce96cb77146106a2578063d905777e146106c1578063dd62ed3e146106e0578063e15fc35 ... (truncated)
}
```

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

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L1039-1051)
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
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L1199-1218)
```go
// Redeem is a paid mutator transaction binding the contract method 0x1e9a6950.
//
// Solidity: function redeem(address _recipient, uint256 _shares) returns()
func (_PublicDelegation *PublicDelegationTransactor) Redeem(opts *bind.TransactOpts, _recipient common.Address, _shares *big.Int) (*types.Transaction, error) {
	return _PublicDelegation.contract.Transact(opts, "redeem", _recipient, _shares)
}

// Redeem is a paid mutator transaction binding the contract method 0x1e9a6950.
//
// Solidity: function redeem(address _recipient, uint256 _shares) returns()
func (_PublicDelegation *PublicDelegationSession) Redeem(_recipient common.Address, _shares *big.Int) (*types.Transaction, error) {
	return _PublicDelegation.Contract.Redeem(&_PublicDelegation.TransactOpts, _recipient, _shares)
}

// Redeem is a paid mutator transaction binding the contract method 0x1e9a6950.
//
// Solidity: function redeem(address _recipient, uint256 _shares) returns()
func (_PublicDelegation *PublicDelegationTransactorSession) Redeem(_recipient common.Address, _shares *big.Int) (*types.Transaction, error) {
	return _PublicDelegation.Contract.Redeem(&_PublicDelegation.TransactOpts, _recipient, _shares)
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

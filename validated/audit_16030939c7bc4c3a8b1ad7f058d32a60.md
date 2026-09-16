## Analysis Result

I found a genuine analog in `PublicDelegation.sol` (Kaia's ERC-4626-style validator delegation vault, only available as Go bindings in `contracts/bindings/publicdelegation/PublicDelegation.go` — the Solidity source is not in the indexed codebase, so I could not verify the exact internal accounting logic; this is a limitation of the available index).

### Title
Transferable PublicDelegation shares combined with per-owner withdrawal-request bookkeeping can let a share recipient be unable to redeem, or let a sender double-claim - (File: contracts/bindings/publicdelegation/PublicDelegation.go)

### Summary
`PublicDelegation` exposes a standard transferable ERC20 interface (`Transfer`, `TransferFrom`, `Approve`, `BalanceOf`, `Allowance`) [1](#0-0)  layered on top of ERC-4626-style vault semantics (`PreviewDeposit`, `PreviewRedeem`, `PreviewWithdraw`, `MaxRedeem`, `MaxWithdraw`) [2](#0-1) . Withdrawal is a two-step, request-based flow: a `RequestWithdrawal` event records `(user, recipient, requestId, assets)` and the mapping `requestIdToOwner(requestId) → owner` associates the pending claim to a specific owner address rather than to the share token itself [3](#0-2) [4](#0-3) . Per-owner request enumeration functions (`GetUserRequestCount`, `GetUserRequestIds`, `GetUserRequestIdsWithState`) further confirm that withdrawal-request state is keyed by address, separate from the transferable share balance [5](#0-4) .

### Finding Description
This mirrors the reported bug class: a transferable pool-share token (`LP token` in the report; `PublicDelegation` share here) whose redemption eligibility is tracked in a mapping keyed by the depositor's address (`requestIdToOwner`, `getUserRequestIds`) instead of being bound to the transferred token itself. If share transfers are permitted after a withdrawal request is created against the sender's address, then:
- Transferring shares to a new holder does not transfer the corresponding `requestIdToOwner` entry, so the recipient cannot claim/finalize a withdrawal they never initiated, while the original owner (who may no longer hold the shares) still owns the `requestId`.
- Conversely, if `MaxRedeem`/`MaxWithdraw` limits are computed purely from current share balance without decrementing for shares already committed to a pending request, a sender could request a withdrawal, then transfer the same (still-held) shares elsewhere before the request settles, effectively getting both a pending withdrawal claim and a live transferable share balance for the same underlying stake — a form of double counting of value.

Because the actual internal accounting logic (e.g., whether shares are burned atomically at `requestWithdrawal` time, or held in escrow, or whether transfer is blocked while a request is pending) is not present in the indexed Go bindings and no Solidity source for `PublicDelegation` is available in this repo, I cannot confirm from code alone whether the contract already guards against this. This is stated explicitly as an open verification gap.

### Impact Explanation
If unguarded, this leads to concrete unauthorized value movement: a party could receive delegation shares via a normal `Transfer`/`TransferFrom` call and be permanently unable to redeem the underlying staked KAIA and rewards, or (worse) the original depositor could retain a redeemable claim after having sold/transferred away the shares that represented it, extracting value twice from the same staked principal. Given `PublicDelegation` sits on real staked KAIA and reward distribution (`Reward()`, `UpdateCommissionTo`), this affects fund custody for any public delegator, meeting Medium/High impact.

### Likelihood Explanation
Likelihood is Medium: `PublicDelegation` shares appear to be freely transferable ERC20 tokens (standard `transfer`/`transferFrom` selectors are present) and are reachable by any unprivileged user who holds shares — no privileged role is required to trigger a transfer or a withdrawal request. Exploitation requires only the same ordinary sequence described in the original report (deposit → request withdrawal → transfer shares), which is easily reachable via ordinary transactions.

### Recommendation
- Verify (in the actual `PublicDelegation.sol` source, not available in this index) whether `requestWithdrawal` atomically burns/locks the exact shares committed to a request, and whether `transfer`/`transferFrom` are blocked or capped by `balanceOf(owner) - sharesLockedInPendingRequests(owner)`.
- If not already enforced, override `_beforeTokenTransfer` (or equivalent) to prevent transferring shares that are already committed to a pending withdrawal request, or bind `requestIdToOwner` semantics to the share ownership at redemption time rather than at request time.
- Add invariant tests asserting that `sum(sharesLockedInRequests) + freelyTransferableBalance == totalBalance` for every holder, and that transferring shares cannot orphan or duplicate a pending withdrawal claim.

### Proof of Concept
1. Alice deposits KAIA into `PublicDelegation` and receives transferable shares.
2. Alice calls the request-withdrawal function; a `RequestWithdrawal(user=Alice, recipient=Alice, requestId, assets)` event fires and `requestIdToOwner(requestId) = Alice` [6](#0-5) .
3. If Alice's share balance was not reduced/locked for the assets committed to `requestId` (unverifiable from available bindings), Alice calls `Transfer(Bob, shares)` using the standard ERC20 transfer entrypoint [7](#0-6) .
4. Bob now holds shares but has no `requestId` under his address (`GetUserRequestIds(Bob)` returns empty) [8](#0-7) , while Alice still owns `requestId` and can finalize/claim the withdrawal despite no longer holding the shares — resulting in either Bob's shares being unredeemable or Alice double-extracting value.

**Caveat:** This finding is based on the ABI/binding surface only; I could not locate `PublicDelegation.sol` in this repository to confirm the internal share-locking implementation. A Devin session with full repository access (or the actual deployed bytecode source) should verify whether shares are actually locked/burned at request time before treating this as a confirmed (rather than analog-based) vulnerability.

### Citations

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L619-710)
```go
// GetUserRequestCount is a free data retrieval call binding the contract method 0xc166c458.
//
// Solidity: function getUserRequestCount(address _owner) view returns(uint256)
func (_PublicDelegation *PublicDelegationCaller) GetUserRequestCount(opts *bind.CallOpts, _owner common.Address) (*big.Int, error) {
	var out []interface{}
	err := _PublicDelegation.contract.Call(opts, &out, "getUserRequestCount", _owner)

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}

// GetUserRequestCount is a free data retrieval call binding the contract method 0xc166c458.
//
// Solidity: function getUserRequestCount(address _owner) view returns(uint256)
func (_PublicDelegation *PublicDelegationSession) GetUserRequestCount(_owner common.Address) (*big.Int, error) {
	return _PublicDelegation.Contract.GetUserRequestCount(&_PublicDelegation.CallOpts, _owner)
}

// GetUserRequestCount is a free data retrieval call binding the contract method 0xc166c458.
//
// Solidity: function getUserRequestCount(address _owner) view returns(uint256)
func (_PublicDelegation *PublicDelegationCallerSession) GetUserRequestCount(_owner common.Address) (*big.Int, error) {
	return _PublicDelegation.Contract.GetUserRequestCount(&_PublicDelegation.CallOpts, _owner)
}

// GetUserRequestIds is a free data retrieval call binding the contract method 0x60df7c6c.
//
// Solidity: function getUserRequestIds(address _owner) view returns(uint256[])
func (_PublicDelegation *PublicDelegationCaller) GetUserRequestIds(opts *bind.CallOpts, _owner common.Address) ([]*big.Int, error) {
	var out []interface{}
	err := _PublicDelegation.contract.Call(opts, &out, "getUserRequestIds", _owner)

	if err != nil {
		return *new([]*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new([]*big.Int)).(*[]*big.Int)

	return out0, err

}

// GetUserRequestIds is a free data retrieval call binding the contract method 0x60df7c6c.
//
// Solidity: function getUserRequestIds(address _owner) view returns(uint256[])
func (_PublicDelegation *PublicDelegationSession) GetUserRequestIds(_owner common.Address) ([]*big.Int, error) {
	return _PublicDelegation.Contract.GetUserRequestIds(&_PublicDelegation.CallOpts, _owner)
}

// GetUserRequestIds is a free data retrieval call binding the contract method 0x60df7c6c.
//
// Solidity: function getUserRequestIds(address _owner) view returns(uint256[])
func (_PublicDelegation *PublicDelegationCallerSession) GetUserRequestIds(_owner common.Address) ([]*big.Int, error) {
	return _PublicDelegation.Contract.GetUserRequestIds(&_PublicDelegation.CallOpts, _owner)
}

// GetUserRequestIdsWithState is a free data retrieval call binding the contract method 0x93b89a84.
//
// Solidity: function getUserRequestIdsWithState(address _owner, uint8 _state) view returns(uint256[])
func (_PublicDelegation *PublicDelegationCaller) GetUserRequestIdsWithState(opts *bind.CallOpts, _owner common.Address, _state uint8) ([]*big.Int, error) {
	var out []interface{}
	err := _PublicDelegation.contract.Call(opts, &out, "getUserRequestIdsWithState", _owner, _state)

	if err != nil {
		return *new([]*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new([]*big.Int)).(*[]*big.Int)

	return out0, err

}

// GetUserRequestIdsWithState is a free data retrieval call binding the contract method 0x93b89a84.
//
// Solidity: function getUserRequestIdsWithState(address _owner, uint8 _state) view returns(uint256[])
func (_PublicDelegation *PublicDelegationSession) GetUserRequestIdsWithState(_owner common.Address, _state uint8) ([]*big.Int, error) {
	return _PublicDelegation.Contract.GetUserRequestIdsWithState(&_PublicDelegation.CallOpts, _owner, _state)
}

// GetUserRequestIdsWithState is a free data retrieval call binding the contract method 0x93b89a84.
//
// Solidity: function getUserRequestIdsWithState(address _owner, uint8 _state) view returns(uint256[])
func (_PublicDelegation *PublicDelegationCallerSession) GetUserRequestIdsWithState(_owner common.Address, _state uint8) ([]*big.Int, error) {
	return _PublicDelegation.Contract.GetUserRequestIdsWithState(&_PublicDelegation.CallOpts, _owner, _state)
}
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L853-927)
```go
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

// PreviewRedeem is a free data retrieval call binding the contract method 0x4cdad506.
//
// Solidity: function previewRedeem(uint256 _shares) view returns(uint256)
func (_PublicDelegation *PublicDelegationSession) PreviewRedeem(_shares *big.Int) (*big.Int, error) {
	return _PublicDelegation.Contract.PreviewRedeem(&_PublicDelegation.CallOpts, _shares)
}

// PreviewRedeem is a free data retrieval call binding the contract method 0x4cdad506.
//
// Solidity: function previewRedeem(uint256 _shares) view returns(uint256)
func (_PublicDelegation *PublicDelegationCallerSession) PreviewRedeem(_shares *big.Int) (*big.Int, error) {
	return _PublicDelegation.Contract.PreviewRedeem(&_PublicDelegation.CallOpts, _shares)
}

// PreviewWithdraw is a free data retrieval call binding the contract method 0x0a28a477.
//
// Solidity: function previewWithdraw(uint256 _assets) view returns(uint256)
func (_PublicDelegation *PublicDelegationCaller) PreviewWithdraw(opts *bind.CallOpts, _assets *big.Int) (*big.Int, error) {
	var out []interface{}
	err := _PublicDelegation.contract.Call(opts, &out, "previewWithdraw", _assets)

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}

// PreviewWithdraw is a free data retrieval call binding the contract method 0x0a28a477.
//
// Solidity: function previewWithdraw(uint256 _assets) view returns(uint256)
func (_PublicDelegation *PublicDelegationSession) PreviewWithdraw(_assets *big.Int) (*big.Int, error) {
	return _PublicDelegation.Contract.PreviewWithdraw(&_PublicDelegation.CallOpts, _assets)
}

// PreviewWithdraw is a free data retrieval call binding the contract method 0x0a28a477.
//
// Solidity: function previewWithdraw(uint256 _assets) view returns(uint256)
func (_PublicDelegation *PublicDelegationCallerSession) PreviewWithdraw(_assets *big.Int) (*big.Int, error) {
	return _PublicDelegation.Contract.PreviewWithdraw(&_PublicDelegation.CallOpts, _assets)
}
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L929-958)
```go
// RequestIdToOwner is a free data retrieval call binding the contract method 0xf29177c3.
//
// Solidity: function requestIdToOwner(uint256 _requestId) view returns(address)
func (_PublicDelegation *PublicDelegationCaller) RequestIdToOwner(opts *bind.CallOpts, _requestId *big.Int) (common.Address, error) {
	var out []interface{}
	err := _PublicDelegation.contract.Call(opts, &out, "requestIdToOwner", _requestId)

	if err != nil {
		return *new(common.Address), err
	}

	out0 := *abi.ConvertType(out[0], new(common.Address)).(*common.Address)

	return out0, err

}

// RequestIdToOwner is a free data retrieval call binding the contract method 0xf29177c3.
//
// Solidity: function requestIdToOwner(uint256 _requestId) view returns(address)
func (_PublicDelegation *PublicDelegationSession) RequestIdToOwner(_requestId *big.Int) (common.Address, error) {
	return _PublicDelegation.Contract.RequestIdToOwner(&_PublicDelegation.CallOpts, _requestId)
}

// RequestIdToOwner is a free data retrieval call binding the contract method 0xf29177c3.
//
// Solidity: function requestIdToOwner(uint256 _requestId) view returns(address)
func (_PublicDelegation *PublicDelegationCallerSession) RequestIdToOwner(_requestId *big.Int) (common.Address, error) {
	return _PublicDelegation.Contract.RequestIdToOwner(&_PublicDelegation.CallOpts, _requestId)
}
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L1351-1372)
```go
}

// Transfer is a paid mutator transaction binding the contract method 0xa9059cbb.
//
// Solidity: function transfer(address to, uint256 value) returns(bool)
func (_PublicDelegation *PublicDelegationSession) Transfer(to common.Address, value *big.Int) (*types.Transaction, error) {
	return _PublicDelegation.Contract.Transfer(&_PublicDelegation.TransactOpts, to, value)
}

// Transfer is a paid mutator transaction binding the contract method 0xa9059cbb.
//
// Solidity: function transfer(address to, uint256 value) returns(bool)
func (_PublicDelegation *PublicDelegationTransactorSession) Transfer(to common.Address, value *big.Int) (*types.Transaction, error) {
	return _PublicDelegation.Contract.Transfer(&_PublicDelegation.TransactOpts, to, value)
}

// TransferFrom is a paid mutator transaction binding the contract method 0x23b872dd.
//
// Solidity: function transferFrom(address from, address to, uint256 value) returns(bool)
func (_PublicDelegation *PublicDelegationTransactor) TransferFrom(opts *bind.TransactOpts, from common.Address, to common.Address, value *big.Int) (*types.Transaction, error) {
	return _PublicDelegation.contract.Transact(opts, "transferFrom", from, to, value)
}
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L2762-2794)
```go
// PublicDelegationRequestWithdrawal represents a RequestWithdrawal event raised by the PublicDelegation contract.
type PublicDelegationRequestWithdrawal struct {
	User      common.Address
	Recipient common.Address
	RequestId *big.Int
	Assets    *big.Int
	Raw       types.Log // Blockchain specific contextual infos
}

// FilterRequestWithdrawal is a free log retrieval operation binding the contract event 0xd71e6ec4eed83207b08d7ee4a0773c0ff8f8a1ab94b8ce85737fc0c5ea2b5f0c.
//
// Solidity: event RequestWithdrawal(address indexed user, address indexed recipient, uint256 indexed requestId, uint256 assets)
func (_PublicDelegation *PublicDelegationFilterer) FilterRequestWithdrawal(opts *bind.FilterOpts, user []common.Address, recipient []common.Address, requestId []*big.Int) (*PublicDelegationRequestWithdrawalIterator, error) {

	var userRule []interface{}
	for _, userItem := range user {
		userRule = append(userRule, userItem)
	}
	var recipientRule []interface{}
	for _, recipientItem := range recipient {
		recipientRule = append(recipientRule, recipientItem)
	}
	var requestIdRule []interface{}
	for _, requestIdItem := range requestId {
		requestIdRule = append(requestIdRule, requestIdItem)
	}

	logs, sub, err := _PublicDelegation.contract.FilterLogs(opts, "RequestWithdrawal", userRule, recipientRule, requestIdRule)
	if err != nil {
		return nil, err
	}
	return &PublicDelegationRequestWithdrawalIterator{contract: _PublicDelegation.contract, event: "RequestWithdrawal", logs: logs, sub: sub}, nil
}
```

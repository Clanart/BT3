### Title
Missing slippage protection on PublicDelegation share mint/redeem (deposit/stake/redeem lack minShares/minAssets bounds) - (File: contracts/bindings/publicdelegation/PublicDelegation.go / underlying PublicDelegation.sol)

### Summary
`PublicDelegation` is an ERC-4626-style liquid-staking vault where a user's KAIA deposit is converted into shares using a live, on-chain exchange rate (`convertToShares`/`convertToAssets`, `previewDeposit`/`previewRedeem`), and that rate moves with the vault's total staked assets and accrued `reward()`. The `redeem` entry point takes only a recipient and a share amount, with no caller-supplied minimum-assets-out bound, and the deposit/stake path likewise exposes no minimum-shares-out bound. This mirrors the Equity.sol FPS mint/redeem bug: any transaction that changes total assets or total shares between submission and inclusion (e.g., a preceding stake, redeem, redelegation, or reward settlement) changes the price a pending mint/redeem executes at, with no on-chain guard to abort the transaction if the received amount falls outside the user's expectation.

### Finding Description
`PublicDelegation` exposes an ERC-4626-like share/asset conversion surface: [1](#0-0) [2](#0-1) [3](#0-2) 

The `reward()` accessor confirms that the vault's total-assets base (and thus the shares↔assets exchange rate) fluctuates with staking rewards, independent of any single user's transaction: [4](#0-3) 

The `redeem` transaction entry point accepts only a recipient address and a share amount — there is no `minAssetsOut` parameter to bound the amount of KAIA the caller is willing to accept: [5](#0-4) 

The `Staked`/`Redeemed`-style event (`Staked(user, assets, shares)`) confirms that assets and shares are computed at execution time using the vault's live exchange rate rather than a value pinned by the depositor: [6](#0-5) 

Because the exchange rate (`convertToShares`/`convertToAssets`) is a function of total staked assets and total shares at execution time, any transaction that is inserted before a pending `deposit`/`stake` or `redeem` call (e.g., another user's large stake, redeem, `redelegateByAssets`/`redelegateByShares`, or a reward/commission settlement) shifts the price the victim's transaction executes against. Exactly as in the reported Equity FPS finding, a searcher observing a pending stake/redeem in the mempool can front-run with their own stake/redeem and back-run to capture the price impact, since neither the mint path (deposit/stake) nor the `redeem` path enforces a minimum-shares-out / minimum-assets-out bound.

### Impact Explanation
An attacker (unprivileged transaction sender / MEV searcher) can sandwich victim deposit/redeem transactions against `PublicDelegation` to extract value from the price impact of the victim's own trade, i.e., unauthorized value transfer from the honest depositor to the attacker, and inflated/deflated share issuance relative to what the user intended. This directly matches the "concrete unauthorized value movement" impact category required, and affects any public staker interacting with this system-adjacent staking contract reachable via a single submitted transaction/bundle. Severity is Medium given it requires favorable ordering (MEV/mempool visibility or bundle capability) and vault size/liquidity constraints, similar to the confirmed original finding's rating.

### Likelihood Explanation
Likelihood is moderate: it requires an attacker to observe a pending stake/redeem transaction and insert transactions immediately before/after it (via mempool visibility or an auction/bundle mechanism already present in this codebase, e.g., `kaiax/auction`). No special privileges are needed — this is reachable by any staker or bidder able to control transaction ordering relative to a target's public delegation deposit/redeem call.

### Recommendation
Add caller-supplied slippage bounds to the mint and redemption paths of `PublicDelegation`, analogous to `minAssetsOut` on `redeem`/`withdraw` and `minSharesOut` on `deposit`/`stake` (or an equivalent deadline+bound pattern already used elsewhere in this codebase, e.g., `GaslessSwapRouter.swapForGas`'s `minAmountOut`/`deadline` guard). Revert the transaction if the actual `shares`/`assets` computed at execution time fall short of the caller's specified minimum.

### Proof of Concept
1. Vault state: `totalShares = S`, `totalAssets = A` (assets include staked KAIA + `reward()`).
2. Alice submits `stake`/`deposit` (or `redeem`) for a large amount, expecting `previewDeposit`/`previewRedeem` output computed against current `(A, S)`.
3. A searcher observes Alice's pending transaction and inserts their own `stake` immediately before it, changing `(A, S)` to `(A', S')`, then inserts a `redeem` immediately after Alice's transaction executes, changing `(A', S')` to `(A'', S'')` — extracting the price impact caused by Alice's trade, exactly as demonstrated numerically for Equity FPS in the original report (Alice: 4000 in / 1710 expected shares vs 1370 actual once sandwiched; searcher nets the difference).
4. Because `redeem(address _recipient, uint256 _shares)` and the deposit/stake entry points expose no minimum-output parameter, Alice's transaction cannot revert to protect her from this outcome — it will execute at whatever price results from the searcher's intervening transactions. [5](#0-4)

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

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L853-865)
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
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L867-882)
```go
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

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L960-982)
```go
// Reward is a free data retrieval call binding the contract method 0x228cb733.
//
// Solidity: function reward() view returns(uint256)
func (_PublicDelegation *PublicDelegationCaller) Reward(opts *bind.CallOpts) (*big.Int, error) {
	var out []interface{}
	err := _PublicDelegation.contract.Call(opts, &out, "reward")

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}

// Reward is a free data retrieval call binding the contract method 0x228cb733.
//
// Solidity: function reward() view returns(uint256)
func (_PublicDelegation *PublicDelegationSession) Reward() (*big.Int, error) {
	return _PublicDelegation.Contract.Reward(&_PublicDelegation.CallOpts)
}
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L1199-1211)
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
```

**File:** contracts/bindings/publicdelegation/PublicDelegation.go (L3087-3098)
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
func (_PublicDelegation *PublicDelegationFilterer) FilterStaked(opts *bind.FilterOpts, user []common.Address, assets []*big.Int, shares []*big.Int) (*PublicDelegationStakedIterator, error) {
```

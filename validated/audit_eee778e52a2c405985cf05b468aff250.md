### Title
Direct ERC20 donation to `MasterMagpie` inflates `_calLpSupply()` and permanently dilutes `accMGPPerShare`, freezing unclaimed MGP yield - (File: rewards/MasterMagpie.sol)

### Summary
`_calLpSupply()` computes the reward-accrual denominator for any non-vlMGP/non-mWomSV pool as the raw `IERC20(_stakingToken).balanceOf(address(this))` rather than a tracked sum of credited `UserInfo.amount`. Because staking-token transfers are permissionless, anyone can send staking tokens directly to `MasterMagpie` (bypassing `deposit`) to inflate this balance, and the next call that triggers `updatePool()` (including via `multiclaimSpec`) permanently bakes the diluted `accMGPPerShare` into the pool's accounting, unclaimably losing part of the MGP emission for that interval.

### Finding Description
`updatePool()` computes the fixed MGP reward for an elapsed interval independent of the actual number of tokens legitimately staked: [1](#0-0) 

and divides it by `_calLpSupply(_stakingToken)`, which for ordinary pools is simply the contract's token balance: [2](#0-1) 

Legitimate deposits go through `_deposit()`, which both increases `user.amount`/`user.available` and pulls tokens via `safeTransferFrom`, keeping `balanceOf(this)` in sync with the sum of credited `UserInfo.amount`: [3](#0-2) 

However, nothing prevents an attacker from calling `token.transfer(masterMagpie, amount)` directly on the staking token, which raises `balanceOf(this)` (and thus `_calLpSupply`) without crediting any `UserInfo.amount`. Once `updatePool()` (or `_calMGPReward`) runs while this inflated balance is present — e.g. via the reachable, permissionless `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` entrypoint calling `_multiClaim` → `updatePool(_stakingToken)`: [4](#0-3) [5](#0-4) 

`pool.accMGPPerShare` is permanently updated using the inflated `lpSupply`, and `pool.lastRewardTimestamp` advances. Since `MGP` is a fixed-supply, pre-funded ERC20 (minted only once at construction and transferred out later via `_sendMGP`/`vlmgp.lockFor`) rather than minted per-claim, the emission for that elapsed interval that "should" have gone to `sum(UserInfo.amount * Δ accMGPPerShare)` is instead partly diluted away to the phantom donated balance, which credits no one. Because `lastRewardTimestamp` has already moved forward, this shortfall cannot be recovered retroactively — the MGP corresponding to that interval's diluted share is permanently unclaimable by any staker, while remaining unaccounted for in the contract's MGP balance (no sweep/rescue path is present for this scenario).

### Impact Explanation
Any staker in an affected pool (present or future) permanently receives a lower `accMGPPerShare` increment than the emission schedule (`mgpPerSec`, `allocPoint`) intends, because the denominator no longer matches the credited stake. The MGP corresponding to the diluted portion is never credited to any `UserInfo.rewardDebt`/`unClaimedMgp`, so it is effectively frozen/lost yield for the legitimate depositors of that pool — matching the "Permanent freezing of unclaimed yield" impact class, reachable purely through a raw token transfer plus a call to the permissionless `multiclaimSpec`/`updatePool` path with no special roles required.

### Likelihood Explanation
The precondition is trivial: the attacker only needs to hold the staking token (bought on the open market) and send it directly to `MasterMagpie`, then trigger `updatePool` (directly or via `multiclaimSpec`/`deposit`/`withdraw` from anyone). No privileged role, flash loan, or complex setup is required, and the action is repeatable on every pool that uses the default `_calLpSupply` branch (any pool other than the vlMGP/mWomSV pools, which instead use `totalSupply()` and are unaffected).

### Recommendation
Track staked supply internally (e.g., a `totalStaked[_stakingToken]` counter incremented/decremented in `_deposit`/`_withdraw`) instead of relying on `IERC20(_stakingToken).balanceOf(address(this))` in `_calLpSupply()`, so direct token donations cannot influence the reward-accrual denominator.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `MasterMagpie`, an MGP-fungible reward token, and an ERC20 staking token; register the staking-token pool with a nonzero `allocPoint`.
2. Attacker deposits `X` tokens via `deposit()` (credited `UserInfo.amount = X`, `balanceOf(MasterMagpie) = X`).
3. Advance time; attacker directly `transfer()`s an additional `Y` staking tokens straight to `MasterMagpie` (no `deposit()` call), so `balanceOf(MasterMagpie) = X + Y` but `UserInfo.amount` is unchanged.
4. Call `multiclaimSpec([stakingToken], [[]])` (or any path hitting `updatePool`), which computes `accMGPPerShare` using `lpSupply = X + Y`.
5. Assert `pool.accMGPPerShare * X / 1e12` (total claimable) is strictly less than the MGP amount the emission schedule (`multiplier * mgpPerSec * allocPoint / totalAllocPoint`) intended to distribute for that interval, and that the shortfall is not recoverable in any later state (repeat interval without further donation and confirm the deficit persists), demonstrating permanently frozen/unclaimable yield.

### Citations

**File:** rewards/MasterMagpie.sol (L374-396)
```text
    function updatePool(address _stakingToken) public whenNotPaused {
        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        if (block.timestamp <= pool.lastRewardTimestamp || totalAllocPoint == 0) {
            return;
        }
        uint256 lpSupply = _calLpSupply(_stakingToken);
        if (lpSupply == 0) {
            pool.lastRewardTimestamp = block.timestamp;
            return;
        }        
        uint256 multiplier = block.timestamp - pool.lastRewardTimestamp;
        uint256 mgpReward = (multiplier * mgpPerSec * pool.allocPoint) / totalAllocPoint;
        
        pool.accMGPPerShare = pool.accMGPPerShare + ((mgpReward * 1e12) / lpSupply);
        pool.lastRewardTimestamp = block.timestamp;

        emit UpdatePool(
            _stakingToken,
            pool.lastRewardTimestamp,
            lpSupply,
            pool.accMGPPerShare
        );
    }    
```

**File:** rewards/MasterMagpie.sol (L406-410)
```text
    function multiclaimSpec(address[] calldata _stakingTokens, address[][] memory _rewardTokens)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, msg.sender, msg.sender, _rewardTokens);
    }
```

**File:** rewards/MasterMagpie.sol (L482-505)
```text
    function _deposit(address _stakingToken, address _account, uint256 _amount, bool _isVlmgp) internal {
        updatePool(_stakingToken);

        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        UserInfo storage user = userInfo[_stakingToken][_account];

        if (user.amount > 0) {
            _harvestMGP(_stakingToken, _account);
        }
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount + _amount;
        if (!_isVlmgp) {
            user.available = user.available + _amount;
            IERC20(pool.stakingToken).safeTransferFrom(address(msg.sender), address(this), _amount);
        }
        user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12;

        if (_amount > 0)
            if (!_isVlmgp)
                emit Deposit(_account, _stakingToken, _amount);
            else
                emit DepositNotAvailable(_account, _stakingToken, _amount);
    }
```

**File:** rewards/MasterMagpie.sol (L544-550)
```text
        for (uint256 i = 0; i < length; ++i) {
            address _stakingToken = _stakingTokens[i];
            UserInfo storage user = userInfo[_stakingToken][_user];
            
            updatePool(_stakingToken);
            uint256 claimableMgp = _calNewMGP(_stakingToken, _user) + unClaimedMgp[_stakingToken][_user];

```

**File:** rewards/MasterMagpie.sol (L659-667)
```text
    function _calLpSupply(address _stakingToken) internal view returns (uint256) {
        if (_stakingToken == address(vlmgp)) {
            return IERC20(address(vlmgp)).totalSupply();
        }
        if (_stakingToken == address(mWomSV)) {
            return IERC20(address(mWomSV)).totalSupply();
        }
        return IERC20(_stakingToken).balanceOf(address(this));
    }
```

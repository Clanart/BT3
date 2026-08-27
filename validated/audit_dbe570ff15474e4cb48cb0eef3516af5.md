### Title
lpSupply/totalStaked inflation via direct token donation to `MasterMagpie` — permanent dilution of MGP and bonus-reward accrual - (File: rewards/MasterMagpie.sol)

### Summary
`MasterMagpie._calLpSupply()` uses `IERC20(_stakingToken).balanceOf(address(this))` as the reward-per-share denominator for every pool except vlMGP and mWomSV, and `BaseRewardPool.totalStaked()` mirrors the same pattern (`IERC20(stakingToken).balanceOf(operator)`), while `BaseRewardPool.balanceOf(_account)` is sourced from `MasterMagpie.stakingInfo` (i.e., `UserInfo.amount`). Because an attacker can transfer the pool's staking token (a Wombat receipt token minted by `WombatStaking`) directly to the `MasterMagpie` contract without going through `deposit()`, they can inflate the denominator used in both `updatePool()`'s `accMGPPerShare` update and `BaseRewardPool._provisionReward()`'s `rewardPerTokenStored` update, without crediting any `UserInfo.amount` or any user's `balanceOf()`.

### Finding Description
- `_calLpSupply()` at [1](#0-0)  returns raw `balanceOf(address(this))` for any staking token that isn't vlMGP or mWomSV.
- `updatePool()` divides the per-interval MGP emission by this value: [2](#0-1) .
- `_deposit()` normally keeps `balanceOf(this)` in lockstep with `sum(UserInfo.amount)` because it calls `safeTransferFrom` for exactly `_amount` and increments `user.amount` by the same amount: [3](#0-2) . Likewise `_withdraw`/`_harvestAndUnstake` decrement both in lockstep: [4](#0-3) .
- However, an attacker does **not** need to call `deposit()` at all — a bare `IERC20(stakingToken).transfer(address(masterMagpie), amount)` breaks this invariant directly, since `_calLpSupply()`/`totalStaked()` read the token's `balanceOf`, not an internally tracked accumulator.
- The same anti-pattern exists in `BaseRewardPool.totalStaked()` (`IERC20(stakingToken).balanceOf(operator)`) versus `BaseRewardPool.balanceOf(_account)` which is sourced from `MasterMagpie.stakingInfo` (i.e., `UserInfo.amount`): [5](#0-4) . This is used to compute `rewardPerTokenStored` in `_provisionReward()`: [6](#0-5) .
- There is no `rescue`/`sweep`/`skim` function found in `MasterMagpie.sol` or `BaseRewardPool.sol` to recover or reconcile a donated/orphaned balance, and no code path credits donated tokens to any `UserInfo.amount` after the fact.
- Consequence: every future MGP/bonus-reward emission for that pool is permanently divided by an inflated denominator, so a portion of every subsequent reward tranche becomes mathematically undistributable to any `UserInfo`/`balanceOf(user)` — it is diluted away rather than distributed, and there is no mechanism to later attribute or reclaim it.

### Impact Explanation
This breaks the stated invariant that MGP/bonus rewards emitted over an interval must be fully distributable to `sum(UserInfo.amount)` and that `accMGPPerShare`/`rewardPerTokenStored` must only be divided by staked-and-credited supply. The practical effect is a permanent reduction in the reward rate received by all legitimate stakers of the targeted pool for as long as the donated balance remains uncredited (which is forever, absent an admin/owner action not modeled here), matching "permanent freezing of unclaimed yield" for the diluted portion of every future reward distribution. Note this is a griefing/dilution effect against existing stakers rather than a direct extraction of funds by the attacker; the attacker's donated tokens are also not recoverable (since they were never credited to their own `UserInfo.amount`), so the attacker does not profit — but they can permanently degrade yield for other legitimate stakers of that specific pool at the cost of the tokens donated.

### Likelihood Explanation
- No privileged role is required; any EOA/contract holding the pool's staking token (a Wombat receipt token from `WombatStaking`) can execute this by a single unprivileged `transfer()` call to the `MasterMagpie` contract address.
- No `nonReentrant`/`whenNotPaused` guard applies since the exploit doesn't call any `MasterMagpie` function at all — it is a plain ERC20 transfer, entirely outside `MasterMagpie`'s access control surface.
- The action is repeatable at will, and the effect is cumulative/permanent for the pool since there is no reconciliation mechanism found.
- Capital cost equals the amount of staking token donated, which is a sunk cost to the attacker but proportionally cheap for the dilution achieved (dilution scales with pool size only).

### Recommendation
Replace `balanceOf(address(this))`-based accounting in `_calLpSupply()` (rewards/MasterMagpie.sol) and `totalStaked()` (rewards/BaseRewardPool.sol) with an internally tracked state variable (e.g., a `totalStaked`/`lpSupply` mapping) that is incremented/decremented only inside `_deposit`/`_withdraw` (and `depositFor`/`withdrawFor`), so it is immune to direct token donations. Any pre-existing balance discrepancy should be swept via an owner-gated recovery function that does not affect `accMGPPerShare`/`rewardPerTokenStored`.

### Proof of Concept
Hardhat test plan:
1. Deploy `MasterMagpie`, register a pool with a mock ERC20 staking token (18 decimals) and a `BaseRewardPool` rewarder, set `mgpPerSec` and `allocPoint`.
2. User A calls `deposit(stakingToken, 100e18)`.
3. Run A: advance time by `T`, call `updatePool`, record `accMGPPerShare_A` and `IBaseRewardPool.rewardPerToken(bonusToken)` after queuing a bonus reward via `queueNewRewards`.
4. Run B (fresh deployment, same initial state): User A calls `deposit(stakingToken, 100e18)`; then an attacker directly calls `stakingToken.transfer(masterMagpieAddress, 100e18)` (no `deposit()` call); advance time by same `T`, call `updatePool`, queue the same bonus reward amount, record `accMGPPerShare_B` and `rewardPerToken(bonusToken)_B`.
5. Assert: `accMGPPerShare_B < accMGPPerShare_A` and `rewardPerToken(bonusToken)_B < rewardPerToken(bonusToken)_A`, while `IBaseRewardPool(rewarder).balanceOf(userA)` and `UserInfo.amount` for A are identical in both runs, and `IBaseRewardPool(rewarder).totalStaked()` differs (200e18 in run B vs 100e18 in run A) despite no new credited stake — demonstrating the denominator inflation and permanent yield dilution for User A caused solely by an unprivileged direct transfer.

### Citations

**File:** rewards/MasterMagpie.sol (L379-388)
```text
        uint256 lpSupply = _calLpSupply(_stakingToken);
        if (lpSupply == 0) {
            pool.lastRewardTimestamp = block.timestamp;
            return;
        }        
        uint256 multiplier = block.timestamp - pool.lastRewardTimestamp;
        uint256 mgpReward = (multiplier * mgpPerSec * pool.allocPoint) / totalAllocPoint;
        
        pool.accMGPPerShare = pool.accMGPPerShare + ((mgpReward * 1e12) / lpSupply);
        pool.lastRewardTimestamp = block.timestamp;
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

**File:** rewards/MasterMagpie.sol (L507-534)
```text
    /// @notice internal function to deal with withdraw staking token
    function _withdraw(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        _harvestAndUnstake(_stakingToken, _account, _amount, _isVlMgp);

        if (!_isVlMgp)
            IERC20(tokenToPoolInfo[_stakingToken].stakingToken).safeTransfer(address(msg.sender), _amount);
        emit Withdraw(_account, _stakingToken, _amount);
    }

    function _harvestAndUnstake(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        updatePool(_stakingToken);

        UserInfo storage user = userInfo[_stakingToken][_account];

        if (!_isVlMgp && user.available < _amount)
            revert WithdrawAmountExceedsStaked();
        else if(user.amount < _amount && _isVlMgp)
            revert UnlockAmountExceedsLocked();
        
        _harvestMGP(_stakingToken, _account);
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount - _amount;
        
        if(!_isVlMgp)
            user.available = user.available - _amount;
        user.rewardDebt = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) / 1e12;
    }
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

**File:** rewards/BaseRewardPool.sol (L124-136)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }

    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/BaseRewardPool.sol (L297-319)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
        );
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;
        if (this.totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**stakingDecimals()) /
                this.totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
```

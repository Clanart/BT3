## Analysis

The reported bug class is a state-mutating function that skips a checkpoint-sync step performed by its sibling function, so a subsequent reward/fee computation is based on stale data. An analogous pattern exists in `MasterMagpie.emergencyWithdraw`.

### Title
Unsynced `BaseRewardPool` checkpoint in `MasterMagpie.emergencyWithdraw` permanently freezes accrued bonus-reward yield - (`rewards/MasterMagpie.sol`)

### Summary
`MasterMagpie.emergencyWithdraw` reduces a user's staked `amount`/`available` balance without first synchronizing the associated `BaseRewardPool` reward checkpoint (`updateFor`), unlike the normal `_deposit`/`_withdraw` paths which always call `_harvestBaseRewarder` before mutating balances. Because `BaseRewardPool.balanceOf` reads live balance data from `MasterMagpie`, once the balance is reduced to zero without a prior checkpoint sync, any bonus reward accrued since the user's last sync becomes permanently unclaimable.

### Finding Description
In the normal deposit/withdraw flow, both `_deposit` and `_harvestAndUnstake` (used by `_withdraw`) call `_harvestBaseRewarder`, which invokes `rewarder.updateFor(_account)` before the user's `amount`/`available` is changed: [1](#0-0) [2](#0-1) 

`_harvestBaseRewarder` is what triggers the checkpoint sync: [3](#0-2) 

However, `emergencyWithdraw` mutates `user.available` and `user.amount` directly, with no call to `updatePool`, `_harvestMGP`, or `_harvestBaseRewarder`: [4](#0-3) 

`BaseRewardPool.balanceOf` is a *live* read from `MasterMagpie.stakingInfo`, not an internally-cached balance: [5](#0-4) 

Reward accrual is computed on-demand from this live balance combined with the last-saved checkpoint: [6](#0-5) 

and the checkpoint (`userRewards` / `userRewardPerTokenPaid`) is only updated inside `_updateFor`, which is only invoked via `updateFor`, `getReward`, or (during normal flows) `_harvestBaseRewarder`: [7](#0-6) 

Because `emergencyWithdraw` never calls `updateFor` before zeroing out the user's `available`/`amount`, any bonus reward token accrued between the user's last checkpoint sync and the emergency withdrawal is computed as `balanceOf(account) * (rewardPerToken - userRewardPerTokenPaid) + userRewards[account]`. Once `balanceOf` drops to `0` as a direct side effect of `emergencyWithdraw`, that first term permanently evaluates to `0` for the un-synced interval, and the accrued portion is never captured into `userRewards`, so it is lost forever.

### Impact Explanation
This causes permanent freezing/loss of unclaimed bonus-reward-token yield for any user who calls `emergencyWithdraw` while a reward-emitting `BaseRewardPool` is attached to their pool. This matches the accepted "theft or permanent freezing of unclaimed yield" impact category.

### Likelihood Explanation
`emergencyWithdraw` is only callable `whenPaused`, i.e. after the (non-malicious, ordinary maintenance) admin action of pausing the contract; the withdrawal call itself is made directly by an ordinary, unprivileged user account, so this is a realistic, permissionless trigger whenever the pool is paused (e.g., during upgrades or incident response) and any bonus rewards are actively accruing on the pool.

### Recommendation
Add a call to `updatePool(_stakingToken)` and `_harvestBaseRewarder(_stakingToken, msg.sender)` (or at minimum `rewarder.updateFor(msg.sender)`) at the start of `emergencyWithdraw`, mirroring the checkpoint sync performed in `_deposit`/`_harvestAndUnstake`, before `user.available`/`user.amount` are reduced.

### Proof of Concept
1. Pool `P` has a `BaseRewardPool` rewarder actively distributing bonus token `R` via `queueNewRewards`/`donateRewards`.
2. User deposits into `P` via `MasterMagpie.deposit`, which syncs the `BaseRewardPool` checkpoint (`userRewardPerTokenPaid` set to current `rewardPerToken`).
3. Time passes; `rewardPerToken` for `R` increases as more `R` is queued, so the user has real pending `earned(user, R) > 0` (still unsynced into `userRewards`).
4. Admin (non-maliciously) pauses `MasterMagpie` for maintenance.
5. User calls `emergencyWithdraw(P)`, which sets `user.available = 0` and `user.amount -= availableAmount` without calling `updateFor` on the rewarder.
6. `BaseRewardPool.balanceOf(user)` now returns `0` (live read from `MasterMagpie.stakingInfo`).
7. Any later call that triggers `_updateFor(user)` (e.g. a fresh deposit followed by `getReward`) computes `earned = 0 * (...) + userRewards[R][user]` where `userRewards[R][user]` was never advanced past the step-2 checkpoint — the reward accrued between step 3 and step 5 is permanently lost, with no way to recover it, while `totalStaked()`/`rewardPerTokenStored` in the pool already accounted for it being owed.

### Citations

**File:** rewards/MasterMagpie.sol (L434-447)
```text
    /// @notice Withdraw all available tokens without caring about rewards. EMERGENCY ONLY. 
    ///         Locked Token can not be emergent withdraw.
    /// @param _stakingToken Staking token of the pool
    /// @dev withdrawFor of the rewarder with the third param at false is an emergency withdraw
    function emergencyWithdraw(address _stakingToken) external whenPaused {
        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        UserInfo storage user = userInfo[_stakingToken][msg.sender];
        uint256 availableaAmount = user.available;
        user.available = 0;
        IERC20(pool.stakingToken).safeTransfer(address(msg.sender), availableaAmount);
        emit EmergencyWithdraw(msg.sender, _stakingToken, availableaAmount);
        user.amount = user.amount - availableaAmount;
        user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12;
    }
```

**File:** rewards/MasterMagpie.sol (L482-498)
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
```

**File:** rewards/MasterMagpie.sol (L516-534)
```text
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

**File:** rewards/MasterMagpie.sol (L631-636)
```text
    /// only update the reward counting on in base rewarder but not sending them to user
    function _harvestBaseRewarder(address _stakingToken, address _account) internal {
        IBaseRewardPool rewarder = IBaseRewardPool(tokenToPoolInfo[_stakingToken].rewarder);
        if (address(rewarder) != address(0))
            rewarder.updateFor(_account);
    }
```

**File:** rewards/BaseRewardPool.sol (L130-136)
```text
    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/BaseRewardPool.sol (L169-185)
```text
    /// @notice Returns amount of reward token earned by a user
    /// @param _account Address account
    /// @param _rewardToken Address reward token
    /// @return Returns amount of reward token earned by a user
    function earned(address _account, address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return (
            (((balanceOf(_account) *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                (10**stakingDecimals())) + userRewards[_rewardToken][_account])
        );
    }
```

**File:** rewards/BaseRewardPool.sol (L288-295)
```text
    function _updateFor(address _account) internal {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            userRewards[rewardToken][_account] = earned(_account, rewardToken);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
    }
```

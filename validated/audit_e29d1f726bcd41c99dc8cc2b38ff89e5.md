### Title
Insufficient reward-token balance in `BaseRewardPool.getReward()` can permanently freeze user withdrawals in `MasterMagpie` - (File: rewards/BaseRewardPool.sol, rewards/MasterMagpie.sol)

### Summary
`MasterMagpie.withdraw()`/`withdrawFor()` unconditionally harvest all registered bonus reward pools for the staking token before transferring the user's principal back. `BaseRewardPool.getReward()` performs an unchecked `safeTransfer` of the user's full pending reward amount with no check that the contract actually holds that balance. If a reward token's balance in a `BaseRewardPool` is insufficient (e.g., manager stops queuing rewards, external donation dries up, or accounting drifts), the `safeTransfer` reverts, and because `getReward()` is invoked synchronously inside the withdraw path, the entire `withdraw()` transaction reverts — trapping the user's staked principal along with the reward, exactly mirroring the `ComplexRewarder`/child-rewarder insolvency pattern in the external report.

### Finding Description
`MasterMagpie._withdraw()` calls `_harvestAndUnstake()`, which calls `_harvestBaseRewarder(_stakingToken, _account)` before adjusting user balances and transferring the staking token back to the user: [1](#0-0) 

`_harvestBaseRewarder` (and `_multiClaim`'s `_claimBaseRewarder`) ultimately invoke `IBaseRewardPool(rewarder).getReward(_account, _receiver)` on every registered rewarder for that staking token — this call is not wrapped in any try/catch.

`BaseRewardPool.getReward()` transfers the full computed `reward` amount unconditionally: [2](#0-1) 

Unlike `MultiRewarderPerSec.onReward()`, which explicitly caps the transferred amount to `rewardToken.balanceOf(address(this))` and carries the shortfall forward in `user.unpaidRewards` so it never reverts: [3](#0-2) 

`BaseRewardPool.getReward()` has no such balance check or fallback — it will simply revert via `SafeERC20.safeTransfer` if `IERC20(rewardToken).balanceOf(address(this)) < reward`. `BaseRewardPoolV2` and `mWOMSVBaseRewarder` share the same `getReward`/`_sendReward` structure and are equally exposed: [4](#0-3) 

Because `getReward()` is called with `onlyMasterMagpie` from inside the withdraw flow rather than being isolated, any single under-funded reward token for any registered rewarder poisons the entire withdrawal for every user who has non-zero pending rewards in that pool — precisely the "child rewarder" failure mode described in the external report, just realized through `BaseRewardPool`'s reward transfer instead of a literal child-rewarder hierarchy.

### Impact Explanation
A user's `withdraw()`/`withdrawFor()` call permanently reverts as long as the affected `BaseRewardPool`'s reward-token balance remains insufficient to cover the accrued `userRewards`. Since `BaseRewardPool` balances are funded manually via `queueNewRewards`/`donateRewards` by a manager and are not guaranteed to always cover `earned()` (e.g., manager pauses funding, previous rewards were queued for a shorter runway, or token has fee-on-transfer/rebasing behavior reducing actual balance), this can trap user principal in `MasterMagpie` indefinitely — a permanent freeze of funds, not merely of yield, since the staking-token transfer in `_withdraw()` occurs only after the harvest calls succeed.

### Likelihood Explanation
Reward under-funding is a realistic, non-malicious operational condition (a manager simply not re-queuing rewards in time, or rewards being fully claimed by others depleting the balance before a straggling user's stale `userRewards` accounting catches up) and requires no privileged or malicious action to trigger — it's a natural consequence of the reward-pool accounting model coupled with an unconditional transfer.

### Recommendation
Mirror the defensive pattern already used in `MultiRewarderPerSec.onReward()`: in `BaseRewardPool.getReward()` (and `BaseRewardPoolV2`/`mWOMSVBaseRewarder`), cap the transferred amount to `IERC20(rewardToken).balanceOf(address(this))`, carry any shortfall forward (e.g., an `unpaidRewards` accumulator) instead of reverting, or wrap the reward transfer in a try/catch so that a reward-token shortfall cannot block the underlying staking-token withdrawal.

### Proof of Concept
1. User A deposits staking token into `MasterMagpie` pool P, registered with `BaseRewardPool` R using reward token X.
2. R's manager calls `queueNewRewards` to fund a modest amount of X, then stops funding (or `donateRewards` deposits are withdrawn/consumed by other means such that R's X balance falls below cumulative `historicalRewards` owed).
3. Time passes; `rewardPerToken(X)` for R increases such that `earned(userA, X)` exceeds `IERC20(X).balanceOf(R)`.
4. User A calls `MasterMagpie.withdraw(stakingToken, amount)`.
5. `_withdraw` → `_harvestAndUnstake` → `_harvestBaseRewarder` → `R.getReward(userA, userA)` computes `reward = userRewards[X][userA]` and calls `IERC20(X).safeTransfer(userA, reward)`, which reverts because R's balance of X is less than `reward`.
6. The revert propagates up through `_withdraw`, reverting the entire transaction — User A cannot withdraw their staked principal until R's balance of X is topped up, which is outside their control. [2](#0-1) [5](#0-4)

### Citations

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

**File:** rewards/BaseRewardPool.sol (L219-240)
```text
    /// @notice Calculates and sends reward to user. Only callable by masterMagpie
    /// @param _account Address account
    function getReward(address _account, address _receiver)
        override
        public
        onlyMasterMagpie
        updateReward(_account)
        returns (bool)
    {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            uint256 reward = userRewards[rewardToken][_account]; // updated during updateReward modifier
            if (reward > 0) {
                userRewards[rewardToken][_account] = 0;
                IERC20(rewardToken).safeTransfer(_receiver, reward);
                emit RewardPaid(_account, _receiver, reward, rewardToken);
            }
        }

        return true;
    }
```

**File:** mocks/wombat/MultiRewarderPerSec.sol (L189-201)
```text
                } else {
                    // ERC20 token
                    uint256 tokenBalance = rewardToken.balanceOf(address(this));
                    if (pending > tokenBalance) {
                        rewardToken.safeTransfer(_user, tokenBalance);
                        rewards[i] = tokenBalance;
                        user.unpaidRewards = pending - tokenBalance;
                    } else {
                        rewardToken.safeTransfer(_user, pending);
                        rewards[i] = pending;
                        user.unpaidRewards = 0;
                    }
                }
```

**File:** rewards/BaseRewardPoolV2.sol (L218-235)
```text
    function getReward(address _account, address _receiver)
        public
        onlyMasterMagpie
        updateReward(_account)
        returns (bool)
    {
        uint256 length = rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            uint256 reward = userRewards[rewardToken][_account]; // updated during updateReward modifier
            if (reward > 0) {
                _sendReward(rewardToken, _account, _receiver, reward);
            }
        }

        return true;
    }
```

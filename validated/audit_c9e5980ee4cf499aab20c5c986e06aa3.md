### No vulnerability found for this question.

The described attack path does not produce disproportionate gain. `donateRewards` and `queueNewRewards` both funnel into `_provisionReward`, which increases the global `rewardPerTokenStored` accumulator shared by *all* current stakers proportional to `balanceOf`, not something the caller can privately capture [1](#0-0) . Any staker's `earned()` (including the victim's) is computed against the current `rewardPerToken()` value at the time of their `_updateFor` snapshot [2](#0-1) [3](#0-2) , so whichever order `donateRewards`/`queueNewRewards` land relative to the victim's claim, the accumulator increase is applied identically to every staker weighted by their own balance — the attacker cannot extract more than its own `balanceOf`-proportional share, and in fact donating funds that get redistributed pro-rata to all stakers (including the victim) is a net cost to the attacker unless it holds 100% of the pool.

The `multiclaim` empty-array behavior defaulting to `rewarder.getReward` (claim-all) is intentional, documented behavior ("if not specifying any reward token, just claim them all") [4](#0-3) , not a bypass of any access control, and `_multiClaim` is guarded by `nonReentrant` [5](#0-4) . There is no code path here by which an unprivileged attacker can shift rewards away from a victim beyond its own proportional entitlement; the reward-per-token accounting design inherently conserves the invariant described in the question.

### Citations

**File:** rewards/BaseRewardPool.sol (L173-185)
```text
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

**File:** rewards/BaseRewardPool.sol (L297-320)
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
    }
```

**File:** rewards/MasterMagpie.sol (L536-536)
```text
    function _multiClaim(address[] calldata _stakingTokens, address _user, address _receiver, address[][] memory _rewardTokens) internal nonReentrant {
```

**File:** rewards/MasterMagpie.sol (L620-629)
```text
    function _claimBaseRewarder(address _stakingToken, address _account, address _receiver, address[] memory _rewardTokens) internal {
        IBaseRewardPool rewarder = IBaseRewardPool(tokenToPoolInfo[_stakingToken].rewarder);
        if (address(rewarder) != address(0)) {
            if (_rewardTokens.length > 0)
                rewarder.getRewards(_account, _receiver, _rewardTokens);
            else
                // if not specifiying any reward token, just claim them all
                rewarder.getReward(_account, _receiver);
        }
    }
```

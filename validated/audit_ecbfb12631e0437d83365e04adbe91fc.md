### Title
Reward-per-token truncation in `_provisionReward` permanently locks donated/distributed reward tokens - ([File: rewards/BaseRewardPool.sol], [File: rewards/mWOMSVBaseRewarder.sol], [File: rewards/BaseRewardPoolV2.sol])

### Summary
`BaseRewardPool`, `BaseRewardPoolV2`, and `mWOMSVBaseRewarder` all share the same `_provisionReward` logic that increments `rewardPerTokenStored` by `(_amountReward * 10**decimals) / totalStaked()`. When `_amountReward` is small relative to `totalStaked()`, this integer division truncates to zero, so the reward tokens are pulled into the contract (`safeTransferFrom`) and counted in `historicalRewards`, but never actually credited to any staker via `rewardPerTokenStored`. Unlike the `totalStaked() == 0` branch, which correctly carries the amount forward in `queuedRewards`, there is no analogous carry-forward for this truncation case — the tokens are simply stranded in the contract forever.

### Finding Description
`donateRewards(uint256 _amountReward, address _rewardToken)` in `rewards/BaseRewardPool.sol` and `rewards/mWOMSVBaseRewarder.sol` is callable by **any unprivileged wallet** (its only check is `isRewardToken[_rewardToken]`), and internally calls `_provisionReward`: [1](#0-0) 

```
function donateRewards(uint256 _amountReward, address _rewardToken) external {
    if (!isRewardToken[_rewardToken])
        revert MustBeRewardToken();
    _provisionReward(_amountReward, _rewardToken);
}
```

`_provisionReward` performs the transfer and the reward-per-token accounting: [2](#0-1) 

```
function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
    IERC20(_rewardToken).safeTransferFrom(msg.sender, address(this), _amountReward);
    Reward storage rewardInfo = rewards[_rewardToken];
    rewardInfo.historicalRewards = rewardInfo.historicalRewards + _amountReward;
    if (this.totalStaked() == 0) {
        rewardInfo.queuedRewards += _amountReward;
    } else {
        if (rewardInfo.queuedRewards > 0) {
            _amountReward += rewardInfo.queuedRewards;
            rewardInfo.queuedRewards = 0;
        }
        rewardInfo.rewardPerTokenStored =
            rewardInfo.rewardPerTokenStored +
            (_amountReward * 10**stakingDecimals()) / this.totalStaked();
    }
    emit RewardAdded(_amountReward, _rewardToken);
}
```

When `totalStaked()` is nonzero but `_amountReward` is small enough that `(_amountReward * 10**stakingDecimals()) / totalStaked() == 0`, the tokens have already been transferred into the contract, `historicalRewards` is bumped, but `rewardPerTokenStored` is unchanged. Because `rewardInfo.queuedRewards` is only reset/used to carry forward in the `totalStaked() == 0` branch, none of these truncated tokens are added back to `queuedRewards` for a future retry — they are permanently un-accounted for in the reward-per-share ledger and become unclaimable by any staker. The exact same `_provisionReward` pattern (with no minimum-amount guard) is duplicated in `rewards/mWOMSVBaseRewarder.sol` (lines 305-328) and a near-identical pattern (`_amountReward * 10**stakingTokenDecimals / totalStaked()`) exists in `rewards/BaseRewardPoolV2.sol` (lines 290-314) and `rewards/DelegateVoteRewardPool.sol` (lines 178-203), all reachable from `queueNewRewards`/`donateRewards` calls made by an ordinary wallet or by the protocol's own periodic reward managers (e.g. WombatStaking harvesting small yield amounts relative to a large `totalStaked()`).

### Impact Explanation
Any reward tokens (protocol-distributed yield via `queueNewRewards`, or externally donated via `donateRewards`) that round down to zero in the `rewardPerTokenStored` update are stuck in the reward-pool contract with no code path to reclaim, redistribute, or re-queue them. This is a direct, permanent freezing/loss of yield: legitimate periodic reward distributions (e.g. small per-cycle amounts of a low-supply reward token relative to a large staked base) will systematically lose dust each cycle with no recovery mechanism, and there is no minimum-amount validation analogous to the referenced `MIN_GNS_WEI_IN` check to prevent this. Given the presence of the very similar bug in the external report (which was rated Medium), and that this pattern is duplicated across four reward-pool contracts used across the MasterMagpie reward stack, the impact is a systemic, protocol-wide permanent loss of unclaimed yield.

### Likelihood Explanation
Likelihood is Medium: it requires the ratio of `_amountReward * 10**decimals` to `totalStaked()` to fall below 1, which is realistic whenever `totalStaked()` is large (e.g., a heavily staked pool) and reward-token decimals/amount are modest, or whenever a low-value/low-decimal reward token is distributed in small increments. `donateRewards` is directly callable by any wallet with zero minimum-amount protection, so the condition can also be trivially triggered on-demand.

### Recommendation
Add a minimum-effective-amount check in `_provisionReward` (reject or revert if the computed `rewardPerTokenStored` delta would be zero while `_amountReward > 0`), or — mirroring the `totalStaked() == 0` branch — carry any amount that would truncate to zero forward into `rewardInfo.queuedRewards` instead of silently dropping it from the reward-per-token ledger. Apply the fix consistently across `BaseRewardPool.sol`, `BaseRewardPoolV2.sol`, `mWOMSVBaseRewarder.sol`, and `DelegateVoteRewardPool.sol`.

### Proof of Concept
1. Deploy/observe a `BaseRewardPool` pool where `totalStaked()` (staked amount in `operator`) is large, e.g. `totalStaked() = 10_000e18`, and the reward token has 18 decimals.
2. Call `donateRewards(_amountReward, _rewardToken)` (or have the manager call `queueNewRewards`) with `_amountReward = 1` (1 wei) — any unprivileged wallet can do this for `donateRewards` as long as `_rewardToken` is already a registered reward token.
3. Inside `_provisionReward`: `(1 * 10**18) / 10_000e18 = 0`, so `rewardPerTokenStored` is unchanged while `historicalRewards` increases and the 1 wei of reward token is transferred into the contract via `safeTransferFrom`.
4. The 1 wei (and any similarly small future donation/distribution while `totalStaked()` stays large) remains in the contract permanently: it is never reflected in any user's `earned()` calculation and there is no function to reclaim it, since `queuedRewards` is only used when `totalStaked() == 0`.

### Citations

**File:** rewards/BaseRewardPool.sol (L276-284)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
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

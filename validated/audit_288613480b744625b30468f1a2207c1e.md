### Title
Permissionless `donateRewards()` lets a fresh depositor instantly claim an entire backlog of queued rewards - ([File: rewards/BaseRewardPoolV2.sol])

### Summary
`BaseRewardPoolV2` (and identically `mWOMSVBaseRewarder`) already implement the community-recommended mitigation for the "virgin stake claims all drops" bug class: `_provisionReward()` checks `totalStaked() == 0` and, instead of dripping into `rewardPerTokenStored` immediately, buffers the reward into `queuedRewards`. However, this only defers the problem rather than eliminating it: once `totalStaked()` becomes non-zero again, the *entire* backlog of `queuedRewards` (which may represent a long period, or many separate funding events) is dumped into `rewardPerTokenStored` in one shot, using the snapshot `totalStaked()` at that instant. Because `donateRewards()` is a public, unauthenticated function that anyone can call to trigger `_provisionReward()`, an attacker can time a minimal deposit plus a trivial donation to flush the whole queued backlog onto their own tiny stake.

### Finding Description
`_provisionReward()` in [1](#0-0)  queues rewards while `totalStaked() == 0`:
```
if (totalStaked() == 0) {
    rewardInfo.queuedRewards += _amountReward;
} else {
    if (rewardInfo.queuedRewards > 0) {
        _amountReward += rewardInfo.queuedRewards;
        rewardInfo.queuedRewards = 0;
    }
    rewardInfo.rewardPerTokenStored = rewardInfo.rewardPerTokenStored +
        (_amountReward * 10**stakingTokenDecimals) / totalStaked();
}
```
When the branch flips from "queue" to "distribute", the *whole* accumulated `queuedRewards` (potentially built up over an arbitrarily long period with zero stakers, or across many manager `queueNewRewards` calls) is divided by whatever `totalStaked()` happens to be at that exact call — not phased in gradually and not tied to how long any staker has actually been staked.

Critically, `donateRewards()` has no access control beyond requiring the token already be a registered reward token, [2](#0-1) :
```
function donateRewards(uint256 _amountReward, address _rewardToken) external {
    if (!isRewardToken[_rewardToken])
        revert MustBeRewardToken();
    _provisionReward(_amountReward, _rewardToken);
}
```
Any wallet can call it. Combined with `totalStaked()` being read live from `IERC20(stakingToken).balanceOf(operator)` [3](#0-2) , an attacker who is the only (or dominant) depositor at the moment of the flush captures nearly the entire backlog based on their own tiny balance, since `rewardPerTokenStored` is computed against `totalStaked()` at that single block rather than accrued over time as prior legitimate stakers may have expected.

The identical pattern exists in `mWOMSVBaseRewarder._provisionReward` / `_queueNewRewardsWithoutTransfer` [4](#0-3) , and its `donateRewards` is equally unpermissioned [5](#0-4) .

### Impact Explanation
This is a direct, reachable path from an ordinary wallet (deposit into MasterMagpie for the target staking token, then call the permissionless `donateRewards`) that lets an attacker steal previously-queued/unclaimed yield that was intended to accrue to the pool's stakers over time — matching the "theft of unclaimed yield" impact bucket. The attacker only needs to be the current dominant staker at the moment `totalStaked()` transitions from zero (or near-zero) with a non-trivial `queuedRewards` balance, and pay a negligible `_amountReward` (even 1 wei, since `isRewardToken` is the only gate) to trigger the flush in their favor.

### Likelihood Explanation
Likelihood is comparable to the original wxETH finding (rated Medium): it requires `queuedRewards` to have accumulated while `totalStaked() == 0` for that specific staking token/reward token pair — a state that can arise naturally whenever a pool loses all stakers (e.g., mass withdrawal, newly added pool with delayed first depositor) while the reward manager (WombatStaking or another manager) continues periodic `queueNewRewards` calls, or is instead directly engineered by an attacker who is simply first to deposit after such a lull. The permissionless `donateRewards` function removes any dependency on the manager's timing, letting the attacker choose the exact block to trigger the flush themselves.

### Recommendation
Do not distribute the entire `queuedRewards` balance in a single lump sum keyed to the instantaneous `totalStaked()`. Options:
- Vest queued rewards linearly over time (similar to Synthetix-style `rewardRate`/`periodFinish`) rather than releasing them atomically upon the first non-zero `totalStaked()` observation.
- Require a minimum staking duration or a delay/cooldown before newly queued (or previously queued) rewards become claimable by a given depositor.
- Consider gating `donateRewards()` so it cannot be used to opportunistically trigger the flush transition (e.g., disallow donation when `totalStaked()` was zero on the prior update and is only just becoming non-zero in the same or adjacent transaction as a deposit).

### Proof of Concept
1. Staking token pool X has zero stakers (`totalStaked() == 0`); the reward manager periodically calls `queueNewRewards` for reward token R, causing `rewardInfo.queuedRewards` to grow via [6](#0-5) .
2. Attacker calls `MasterMagpie.deposit(X, 1)` (or the minimum non-zero amount), making `totalStaked() = 1` (in `stakingToken` units) [7](#0-6) .
3. Attacker calls `BaseRewardPoolV2.donateRewards(1, R)` — permissionless — which invokes `_provisionReward`, now taking the `totalStaked() > 0` branch and computing `rewardPerTokenStored += (1 + queuedRewards) * 10**decimals / 1`, effectively assigning nearly the entire historical `queuedRewards` bucket to the current `rewardPerTokenStored` [8](#0-7) .
4. Attacker calls `MasterMagpie.multiclaimSpec`/`getReward` to claim the full reward token balance credited to their 1-wei stake, then withdraws their principal, having extracted the entire backlog of queued rewards for a negligible cost.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L126-128)
```text
    function totalStaked() public override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L252-260)
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

**File:** rewards/BaseRewardPoolV2.sol (L290-314)
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

        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**stakingTokenDecimals) /
                totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L296-301)
```text
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }    
```

**File:** rewards/mWOMSVBaseRewarder.sol (L305-346)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20Metadata(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
        );
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;

        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**mWOMSVDecimal) / totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
    }

    function _queueNewRewardsWithoutTransfer(uint256 _amountReward, address _rewardToken) internal
    {
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards = rewardInfo.historicalRewards + _amountReward;
        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**mWOMSVDecimal) / totalStaked();
        }
        emit ForfeitRewardAdded(_amountReward, _rewardToken);
    }
```

**File:** rewards/MasterMagpie.sol (L337-339)
```text
    function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _deposit(_stakingToken, msg.sender, _amount, false);
    }
```

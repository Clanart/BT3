### Title
Reward truncation with no error-carry causes permanent loss of yield in BaseRewardPool / BaseRewardPoolV2 `_provisionReward` - (File: `rewards/BaseRewardPool.sol`, `rewards/BaseRewardPoolV2.sol`)

### Summary
`_provisionReward` (used by both `queueNewRewards` and the fully public `donateRewards`) computes the per-share reward index as `(_amountReward * 10**stakingDecimals()) / totalStaked()`. When `totalStaked()` is large relative to `_amountReward * 10**stakingDecimals()`, integer division truncates the result to `0`, silently discarding the reward increment while `historicalRewards` (and the token balance actually transferred into the pool) is still increased. Unlike the reference `_computeLQTYPerUnitStaked`-style patterns, there is no `lastError`/remainder-carry mechanism to preserve the truncated dust for the next distribution — it is permanently lost, exactly the root cause described in the referenced report.

### Finding Description
In `rewards/BaseRewardPool.sol`:
```solidity
function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
    ...
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
            (_amountReward * 10**stakingDecimals()) /
            this.totalStaked();
    }
    ...
}
``` [1](#0-0) 

The identical pattern exists in `rewards/BaseRewardPoolV2.sol`:
```solidity
rewardInfo.rewardPerTokenStored =
    rewardInfo.rewardPerTokenStored +
    (_amountReward * 10**stakingTokenDecimals) /
    totalStaked();
``` [2](#0-1) 

`queuedRewards` only absorbs amounts when `totalStaked() == 0` — it is never used to carry forward the remainder of `(_amountReward * scale) % totalStaked()` once `totalStaked() > 0`. As soon as the pool has any non-trivial amount staked (which is the normal operating state), every subsequent truncated remainder from the division is dropped permanently: the tokens are pulled into the contract (`safeTransferFrom`) and `historicalRewards` is incremented, but `rewardPerTokenStored` does not reflect the actual amount transferred, so no staker's `earned()` accounting will ever recover that portion. Those tokens remain stuck in the reward pool contract forever.

Critically, this function is reachable directly by an ordinary unprivileged wallet via `donateRewards`, which has **no access control** beyond requiring the token be already registered as a reward token:
```solidity
function donateRewards(uint256 _amountReward, address _rewardToken) external {
    if (!isRewardToken[_rewardToken])
        revert MustBeRewardToken();
    _provisionReward(_amountReward, _rewardToken);
}
``` [3](#0-2) [4](#0-3) 

It is also reached through the normal, non-malicious reward-manager flow via `queueNewRewards`, which is how MasterMagpie/harvest logic routinely funds these pools, meaning even honest protocol operation continuously leaks dust for pools where `totalStaked()` (scaled by `stakingDecimals`) is large relative to typical per-distribution reward amounts (e.g., low-decimal reward tokens like USDC/WBTC being distributed against high-supply LP staking tokens).

### Impact Explanation
Any time `_amountReward * 10**stakingDecimals() < totalStaked()`, the entire donated/queued reward amount is transferred into the contract but contributes `0` to `rewardPerTokenStored`, i.e., it is never distributable to any staker and is permanently frozen in the reward pool contract. This is a permanent freezing of unclaimed yield reachable by any wallet through `donateRewards`, and also silently degrades legitimate reward-manager distributions over time as `totalStaked()` grows, causing accumulated protocol insolvency for depositors expecting full reward distribution.

### Likelihood Explanation
Likelihood is high: `donateRewards` requires no permission and can be called by any address with a small amount of an already-registered reward token, at any time the pool's `totalStaked()` is large enough (which naturally increases over the pool's lifetime as more users stake). No special conditions, race, or privileged role are needed to trigger fund loss — an attacker or even accidental caller sending a "dust" amount will lose it outright, and legitimate reward-manager distributions of low-decimal reward tokens against large-supply staking tokens will progressively leak value the same way.

### Recommendation
Track a `lastRewardError` (or similar) analogous to `lastLQTYError` in the reference mitigation: compute the numerator with the previous remainder added, derive `rewardPerTokenStored` from `numerator / totalStaked()`, and store `numerator - rewardPerTokenStored * totalStaked()` back as the new error to be included in the next distribution, ensuring no dust is permanently lost across `queueNewRewards`/`donateRewards` calls in `BaseRewardPool.sol` and `BaseRewardPoolV2.sol` (and equivalently in `vlMGPBaseRewarder.sol` / `mWOMSVBaseRewarder.sol`, which share the same pattern).

### Proof of Concept
1. Deploy a `BaseRewardPool` (or `BaseRewardPoolV2`) with a staking token of 18 decimals and register a low-decimal reward token (e.g., 6-decimal token).
2. Have stakers deposit through MasterMagpie until `totalStaked()` (18-decimal units) exceeds `1e24` (e.g., 1,000,000 LP tokens staked).
3. Call `donateRewards(9e5, rewardToken)` (or have `queueNewRewards` invoked with a reward amount such that `_amountReward * 1e18 < totalStaked()`), e.g. `9e5 * 1e18 = 9e23 < 1e24`.
4. Observe `rewards[rewardToken].rewardPerTokenStored` is unchanged (increment truncates to 0) while `historicalRewards` increases by `9e5` and the tokens are transferred into the contract balance via `safeTransferFrom`.
5. No staker's `earned()`/`_earned()` reflects this amount; the tokens are permanently stuck since no error-carry mechanism exists to recover them in a later call.

### Citations

**File:** rewards/BaseRewardPool.sol (L279-284)
```text
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
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

**File:** rewards/BaseRewardPoolV2.sol (L255-260)
```text
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L297-313)
```text
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
```

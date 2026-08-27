## Title
Reward accrued before the first staker in `WomUp` is permanently lost - (File: wombat/WomUp.sol)

### Summary
`WomUp::initializeRewards` starts a WOM→MGP reward stream by setting `lastUpdateTime = block.timestamp` and `periodFinish = block.timestamp + duration` regardless of whether anyone has staked yet [1](#0-0) . If `_totalSupply` is still `0` when the stream starts, any time that elapses before the first `stake()` call is never accounted for in `rewardPerTokenStored`, and the corresponding MGP rewards become permanently unclaimable by any user. This mirrors the exact bug class from the external report: `updateRewardsPerWeight`/`rewardPerToken` is advanced before any staker checkpoint exists, silently dropping the reward accrual for the "empty" period.

### Finding Description
The `updateReward` modifier updates `rewardPerTokenStored` and `lastUpdateTime` on every `stake`/`withdraw`/`migrate`/`getReward` call: [2](#0-1) 

`rewardPerToken()` explicitly returns the stored (unchanged) value when `totalSupply() == 0`, instead of accruing anything for the elapsed time: [3](#0-2) 

However, `lastUpdateTime` is unconditionally advanced to `lastTimeRewardApplicable()` (i.e., `min(block.timestamp, periodFinish)`) inside the modifier, with no gating on `totalSupply() > 0`: [2](#0-1) 

Consequently, once `initializeRewards()` sets the reward stream in motion, any elapsed time before the first `stake()` call is "consumed" by advancing `lastUpdateTime`, but the associated reward-per-token is never captured in `rewardPerTokenStored` because the `totalSupply()==0` branch skips it. When the first staker later arrives, the reward-per-token calculation restarts from that later `lastUpdateTime`, so the tokens that accrued during the gap are permanently excluded from all future `earned()` calculations — they remain locked as MGP balance in the contract with no code path to claim them (the `mgp` balance is only distributed via `getReward` → `rewards[account]`, which derives strictly from `rewardPerTokenStored`).

### Impact Explanation
This results in permanent freezing of protocol reward funds (MGP) that have already been earmarked for distribution via `initializeRewards`. The funds sit in the `WomUp` contract with no function (aside from the pre-start-only `rescueReward`, which reverts once `rewardRate > 0`) able to recover or redistribute them. This is a permanent freeze of unclaimed yield, satisfying the funds-freezing impact bar.

### Likelihood Explanation
The scenario requires only normal, non-malicious operational sequencing: the owner calls `initializeRewards()` (a standard, expected admin action to start the reward stream) before any user has called `stake()`, and some time passes before the first staker arrives. No privileged or malicious behavior beyond ordinary contract operation is required, and the vulnerable code path (`stake`) is reachable by any unprivileged wallet.

### Recommendation
Gate the `lastUpdateTime` advancement (and/or the whole reward-accrual side-effects) on `totalSupply() > 0`, or ensure at least one stake occurs before/at the time `initializeRewards()` is called (analogous to the pashov fix referenced in the report: require an existing stake and `_start >= block.timestamp` before activating the reward period). Alternatively, queue/roll over rewards accrued while `totalSupply() == 0`, similar to the `queuedRewards` pattern already used elsewhere in the codebase (`BaseRewardPoolV2::_queueNewRewardsWithoutTransfer` at [4](#0-3) ), so no reward is dropped for a temporarily empty pool.

### Proof of Concept
1. Owner funds `WomUp` with MGP and calls `initializeRewards()`; this sets `lastUpdateTime = T0`, `periodFinish = T0 + duration`, `rewardRate = balance/duration`. At this point `_totalSupply == 0` (no one has staked).
2. Time passes to `T1` (e.g., `T0 + 1 day`) with no stakers.
3. A user calls `stake(amount)`. The `updateReward` modifier executes:
   - `rewardPerToken()` returns `rewardPerTokenStored` unchanged (since `totalSupply()==0` at the time this is evaluated, pre-state-update).
   - `lastUpdateTime` is set to `lastTimeRewardApplicable() = T1`.
4. The reward corresponding to `[T0, T1]` (i.e., `rewardRate * (T1 - T0)`) is never added to `rewardPerTokenStored` and is not reflected in any user's `earned()`.
5. The MGP tokens accrued for `[T0, T1]` remain in the `WomUp` contract balance forever, since `getReward()` only ever pays out based on `rewardPerTokenStored`, which permanently skipped that interval.

### Citations

**File:** wombat/WomUp.sol (L76-84)
```text
    modifier updateReward(address account) {
        rewardPerTokenStored = rewardPerToken();
        lastUpdateTime = lastTimeRewardApplicable();
        if (account != address(0)) {
            rewards[account] = earned(account);
            userRewardPerTokenPaid[account] = rewardPerTokenStored;
        }
        _;
    }
```

**File:** wombat/WomUp.sol (L100-108)
```text
    function rewardPerToken() public view returns (uint256) {
        if (totalSupply() == 0) {
            return rewardPerTokenStored;
        }
        return
            rewardPerTokenStored + (
                (lastTimeRewardApplicable() - (lastUpdateTime)) * rewardRate * (1e18) / (totalSupply())
            );
    }
```

**File:** wombat/WomUp.sol (L200-214)
```text
    function initializeRewards() external onlyOwner returns (bool) {
        if(rewardRate > 0) revert MustZero();

        uint256 rewardsAvailable = IERC20(mgp).balanceOf(address(this));
        if(rewardsAvailable == 0) revert MustNotZero();

        rewardRate = rewardsAvailable / (duration);

        lastUpdateTime = block.timestamp;
        periodFinish = block.timestamp + (duration);

        emit RewardAdded(rewardsAvailable);

        return true;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L301-313)
```text
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

## Title
Permanently frozen `mgp` reward funds due to incorrect `rewardPerToken` accounting when staking begins after `initializeRewards()` in `WomUp.sol` - (File: wombat/WomUp.sol)

### Summary
`WomUp.sol` implements a Synthetix-style, duration-based reward-streaming pool (identical in structure to the `StakingRewardsV3-1.sol` pattern referenced in the external report). If the funding round is started via `initializeRewards()` and the first `stake()` call from an ordinary user happens some time later, the elapsed time during which `totalSupply() == 0` is silently skipped from `rewardPerToken()` accrual, but `rewardRate` and `periodFinish` are fixed at funding time. The reward corresponding to the "empty" period is never later re-added to `rewardPerTokenStored`, so those `mgp` tokens remain permanently stuck in the contract with no function able to recover or redistribute them.

### Finding Description
`initializeRewards()` sets a fixed reward rate over a fixed `duration` window: [1](#0-0) 

`rewardPerToken()` only accrues rewards proportionally to `totalSupply()`; when `totalSupply() == 0` it just returns the stored value without touching `lastUpdateTime`: [2](#0-1) 

However, `lastUpdateTime` **is** advanced every time `updateReward` runs (via `stake`, `withdraw`, `migrate`, `getReward`), including the very first `stake()` call, because it is set unconditionally to `lastTimeRewardApplicable()`: [3](#0-2) [4](#0-3) 

Because `rewardRate` and `periodFinish` are both fixed at `initializeRewards()` time and never recalculated, the reward accrual window that ends at `periodFinish` is effectively shortened by however long it took for the first depositor to arrive, while the total emission budget (`rewardRate * duration`) does not change. The result: `rewardPerTokenStored` only ever accumulates `rewardRate * (periodFinish - firstStakeTime)`, leaving `rewardRate * (firstStakeTime - lastUpdateTime@init)` worth of `mgp` tokens sitting in the contract with no code path to ever attribute them to a user or reclaim them by the owner. `rescueReward()` can only be used before `rewardRate` is set (`block.timestamp >= startTime || rewardRate > 0` reverts), so it cannot recover this residue after the fact: [5](#0-4) 

This is the exact bug class described in the external report: `rewardPerToken`/`rewardPerLiquidity` computed as `(lastTimeRewardApplicable() - lastUpdateTime) * rate`, where a gap between "notify" (here, `initializeRewards`) and the first depositor's stake permanently strands a portion of the reward token balance.

### Impact Explanation
Any `mgp` tokens funded into `WomUp` for a reward round can be partially and irrecoverably lost whenever a normal delay occurs between `initializeRewards()` being called and the first ordinary user calling `stake()`. Since there is no mechanism to re-notify, extend `periodFinish`, or sweep leftover unattributed rewards once `rewardRate > 0`, this constitutes a permanent freezing of protocol/user yield funds, satisfying the "permanent freezing of funds" impact bar.

### Likelihood Explanation
This does not require any malicious or privileged behavior beyond the normal, expected operational flow: the owner calls `initializeRewards()` (a standard, legitimate reward-funding action), and an ordinary unprivileged wallet calls `stake()` at any point afterward — which is completely routine, since there is no guarantee the pool is pre-seeded with a staker at the exact block `initializeRewards()` executes. Any nonzero gap between funding and the first stake reproduces the bug deterministically.

### Recommendation
Recompute `rewardRate` (and/or `lastUpdateTime`) at the time the first staker deposits into an empty pool, rather than fixing `rewardRate`/`periodFinish` solely from `initializeRewards()`. A common fix is to track the accrual start based on the first non-zero `totalSupply()` timestamp, or to require the reward-provisioning call to happen only when `totalSupply() > 0`, or to redistribute any residual balance by allowing the owner to "top up"/re-trigger a new reward period for the unused remainder after `periodFinish`.

### Proof of Concept
1. Owner deposits `mgp` into `WomUp` and calls `initializeRewards()`. This sets `rewardRate = rewardsAvailable / duration`, `lastUpdateTime = T0`, `periodFinish = T0 + duration`.
2. No user stakes for `Δt` seconds (e.g., 2 days out of a 7-day duration). During this time `totalSupply() == 0`, so `rewardPerToken()` returns the stored (zero) value and does not consume the reward budget.
3. At `T0 + Δt`, user Alice calls `stake()`. `updateReward` executes `rewardPerTokenStored = rewardPerToken()` (still 0, since `totalSupply()` was 0 at the time `rewardPerToken()` was evaluated inside the modifier, before `_totalSupply` is incremented) and then sets `lastUpdateTime = lastTimeRewardApplicable() = T0 + Δt`.
4. From here to `periodFinish`, only `rewardRate * (duration - Δt)` worth of `mgp` is ever streamed into `rewardPerTokenStored`. The `rewardRate * Δt` portion (funded originally) is never emitted to any staker.
5. After `periodFinish`, `rewardRate` stays fixed at its original (too-low-for-the-remaining-window-only) value forever; there is no admin function to reclaim or redistribute the stranded `rewardRate * Δt` worth of `mgp`, permanently locking those funds in the `WomUp` contract.

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

**File:** wombat/WomUp.sol (L96-98)
```text
    function lastTimeRewardApplicable() public view returns (uint256) {
        return (block.timestamp < periodFinish ? block.timestamp : periodFinish);
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

**File:** wombat/WomUp.sol (L187-194)
```text
    function rescueReward() public onlyOwner {
        if(block.timestamp >= startTime || rewardRate > 0) revert AlreadyStarted();

        uint256 balance = IERC20(mgp).balanceOf(address(this));
        IERC20(mgp).safeTransfer(owner(), balance);

        emit Rescued();
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

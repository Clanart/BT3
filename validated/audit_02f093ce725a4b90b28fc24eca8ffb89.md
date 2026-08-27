### Title
Permissionless `donateRewards` lets a dust-stake attacker instantly convert pre-existing `queuedRewards` into `rewardPerTokenStored` skewed to themselves, stealing accrued/unclaimed yield - (File: rewards/BaseRewardPool.sol)

### Summary
`donateRewards` is a public, unauthenticated function that only checks the reward token is already registered before calling `_provisionReward`, with no minimum amount, no time-weighting, and no cooldown. An attacker who is the sole (or dominant) staker with a dust `totalStaked` can trigger `_provisionReward`'s conversion of any pending `rewardInfo.queuedRewards` into `rewardPerTokenStored`, dividing by the tiny `totalStaked()`, then immediately withdraw and call `getReward` to claim the entire queued reward pool for themselves.

### Finding Description
`donateRewards` has no access control beyond `isRewardToken[_rewardToken]` and calls `_provisionReward` directly: [1](#0-0) 

`_provisionReward` flushes `rewardInfo.queuedRewards` into `rewardPerTokenStored` whenever `totalStaked() != 0`, dividing by the *current* `totalStaked()`: [2](#0-1) 

`totalStaked()` is a live read of the staking-token balance held by `MasterMagpie`, not a time-weighted or checkpointed value: [3](#0-2) 

`earned()`/`balanceOf()` compute a user's share purely from their current stake times the delta in `rewardPerTokenStored`, with no minimum staking duration: [4](#0-3) 

Exploit flow:
1. `queuedRewards` for a reward token become non-zero whenever rewards are queued (e.g., via `queueNewRewards`) while `totalStaked() == 0` — a normal and reachable state (before the first depositor, or after all stakers have withdrawn).
2. Attacker stakes a dust amount (e.g., 1 wei of the staking token) via `MasterMagpie`, becoming the (near-)sole staker so `totalStaked()` is minuscule.
3. Attacker calls `donateRewards(0 or dust, _rewardToken)` — no `onlyManager` restriction applies here, unlike `queueNewRewards`. This forces `_provisionReward` into the `else` branch, converting all of `rewardInfo.queuedRewards` into `rewardPerTokenStored` divided by the dust `totalStaked()`, producing a hugely inflated `rewardPerTokenStored`.
4. Because `earned()` multiplies this inflated `rewardPerTokenStored` delta by the attacker's own dust `balanceOf`, the arithmetic cancels out and the attacker's `earned` amount equals essentially the entire flushed `queuedRewards`, despite having staked for a negligible amount of time and capital.
5. Attacker calls `MasterMagpie.withdraw` to reclaim their dust stake and `getReward`/`BaseRewardPool.getReward` to claim the full former `queuedRewards` amount. [5](#0-4) 

No existing guard stops this: `donateRewards` lacks `onlyManager`, there is no minimum-stake-duration check, no per-block/per-tx rate limiting on reward-per-token updates, and `queuedRewards` conversion has no protection against a near-zero `totalStaked()` denominator. This differs from `queueNewRewards`, which is restricted to `onlyManager` — but `donateRewards` was intentionally left open for third-party donations and reuses the same vulnerable `_provisionReward` logic without adding safeguards for the dust-stake case.

### Impact Explanation
This is a direct theft of unclaimed/queued yield that rightfully belongs to future or existing stakers who contributed real capital and accrual time. An attacker with negligible capital (1 wei stake) can redirect the entire `queuedRewards` balance of a reward token to themselves in a single transaction bundle, at the expense of the protocol's other stakers who never get their proportional share. This matches Immunefi's "theft of unclaimed yield" / "direct theft of user funds" impact class, since the drained rewards were pooled protocol funds (already transferred into `BaseRewardPool` via prior `queueNewRewards` calls) meant to be distributed pro rata over the staking period.

### Likelihood Explanation
- Preconditions: `rewardInfo.queuedRewards[_rewardToken] > 0` for some registered token, and `totalStaked() ` being small/zero at the time an attacker can enter the pool with a dust deposit — a state reachable in pools with intermittent activity (new pools, low-TVL pools, or right after a mass withdrawal event).
- Capital required: minimal — only a dust amount of the staking token (e.g., 1 wei) and no reward-token capital is strictly required if `donateRewards(0, _rewardToken)` is called (transferring 0 tokens still executes `_provisionReward`'s conversion logic).
- No special privileges are needed: `donateRewards` is fully public, and staking/withdrawing/claiming go through standard `MasterMagpie` user-facing functions.
- Repeatable: this can be executed by any EOA/contract whenever the queued-rewards + dust-totalStaked condition recurs.

### Recommendation
- Restrict the `queuedRewards`-flush logic (or `donateRewards` entirely) so it cannot be triggered by an arbitrary caller when `totalStaked()` is below some meaningful threshold relative to the amount of `queuedRewards` being flushed, or require a minimum bonding/staking duration before rewards become claimable.
- Consider gating `donateRewards` behind `onlyManager`/`onlyRewardManager` like `queueNewRewards`, or separate "donation" accounting so donated funds cannot instantaneously convert pre-existing `queuedRewards` (accumulated while `totalStaked()==0`) into `rewardPerTokenStored` at a manipulated denominator.
- Alternatively, checkpoint `queuedRewards` distribution over time (e.g., linear vesting/streaming) rather than an instantaneous lump-sum conversion tied to the caller-controlled instant in time.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `BaseRewardPool` with a staking token and a registered reward token; deploy mock `MasterMagpie` (or use real one) with attacker and a "manager" account.
2. As manager, call `queueNewRewards(1000e18, rewardToken)` while `totalStaked() == 0` (no one has staked yet) — confirm `rewards[rewardToken].queuedRewards == 1000e18` and `rewardPerTokenStored == 0`.
3. As attacker, stake `1` wei of staking token via `MasterMagpie.deposit`/`stake` for this pool — confirm `totalStaked() == 1`.
4. As attacker, call `donateRewards(0, rewardToken)` (or any small amount) — confirm `queuedRewards` is now 0 and `rewardPerTokenStored` jumped to a huge value (`1000e18 * 10**stakingDecimals / 1`).
5. As attacker, call `MasterMagpie.withdraw(...)` to unstake the 1 wei, then call `getReward`/`MasterMagpie.getReward` — assert attacker receives ~`1000e18` reward tokens (the entire queued reward), despite having staked for a single block with 1 wei.
6. Assert this claimed amount vastly exceeds any "fair" pro-rata share for the elapsed time/stake (fair share ≈ 0), demonstrating the Conservation violation and fund theft from the pool.

### Citations

**File:** rewards/BaseRewardPool.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
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

**File:** rewards/BaseRewardPool.sol (L221-240)
```text
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

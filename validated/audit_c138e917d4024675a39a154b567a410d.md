### Title
First-depositor reward skimming via dust `totalStaked()` in `BaseRewardPool._provisionReward`/`queueNewRewards` - ([File: rewards/BaseRewardPool.sol], [File: rewards/BaseRewardPoolV2.sol])

### Summary
`BaseRewardPool.totalStaked()` reads `IERC20(stakingToken).balanceOf(operator)` directly instead of a checkpointed supply, and `_provisionReward` only special-cases `totalStaked() == 0` (deferring rewards to `queuedRewards`), not near-zero balances. When a pool is freshly deployed or has been fully emptied by existing stakers, an unprivileged attacker can call `deposit(stakingToken, 1)` on `MasterMagpie` right before a manager's `queueNewRewards` transaction, making `totalStaked()` equal to their own dust stake, so `rewardPerTokenStored` is inflated by `_amountReward * 10**decimals / 1` and the attacker captures essentially the entire reward tranche via `getReward`.

### Finding Description
`totalStaked()` in [1](#0-0)  and [2](#0-1)  is defined as `IERC20(stakingToken).balanceOf(operator)`, i.e. the live token balance MasterMagpie holds for that staking token — not an independently accounted total-supply variable.

`_provisionReward` (called from `queueNewRewards`, `onlyManager`) computes: [3](#0-2) 
It only guards the exact `totalStaked() == 0` case by deferring to `queuedRewards`; any nonzero-but-tiny `totalStaked()` (e.g. 1 wei) falls into the `else` branch and produces a hugely inflated `rewardPerTokenStored = (_amountReward * 10**decimals) / totalStaked()`.

`MasterMagpie.deposit` is a public, unprivileged, `whenNotPaused nonReentrant` entrypoint [4](#0-3)  that transfers `_amount` of the staking token in and increases `user.amount`/`user.available`. If the pool is newly created or has been drained back to (near) zero by prior stakers exiting, an attacker can call `deposit(stakingToken, 1)` to become effectively 100% of `totalStaked()`.

Reward accounting: `earned()`/`_earned()` compute `balanceOf(_account) * (rewardPerToken - userRewardPerTokenPaid) / 10**decimals + userRewards`, and `getReward` (onlyMasterMagpie, `updateReward` modifier) pays out `userRewards[token][account]` [5](#0-4) . Because `rewardPerTokenStored` is a single global accumulator updated at the moment of dust `totalStaked()`, the attacker's tiny `balanceOf` multiplied by the inflated `rewardPerToken` delta yields a payout approximating the full `_amountReward`, regardless of how briefly they held their stake. Existing protections (`updateReward` checkpointing, `onlyManager`/`onlyMasterMagpie` modifiers, `nonReentrant`) do not prevent this because they only checkpoint *future* stakers' `userRewardPerTokenPaid` at deposit time — they do not protect the reward computed at the moment of the dust-supply `queueNewRewards` call, and there is no minimum-supply threshold or time-weighted (stake-second) accounting to dilute a single-block depositor's share.

This is exploitable whenever `totalStaked()` for a given staking token is transiently at or near zero right before a manager funds rewards — most realistically at pool launch/first funding, or after a period where all stakers have withdrawn. It is not exploitable against a pool that already holds meaningful, continuously-staked balances, since the attacker cannot forcibly reduce other users' already-staked balances.

### Impact Explanation
Concrete, quantifiable theft of unclaimed yield: the attacker can redirect a manager-funded reward tranche (`_amountReward`) that was intended to accrue to legitimate stakers over time, capturing it almost entirely for a single-block/negligible-capital deposit. This matches the Immunefi impact class "theft of unclaimed yield" / "direct theft of user funds" for the affected staking token's reward pool.

### Likelihood Explanation
Requires a specific precondition: `totalStaked()` (the raw stakingToken balance held by MasterMagpie for that pool) must be exactly zero or near-zero at the moment `queueNewRewards` executes — realistic mainly at pool launch (first reward funding before meaningful TVL accrues) or after a full/near-full withdrawal event. It also requires the attacker to front-run or predict the manager's `queueNewRewards` transaction (mempool visibility of an `onlyManager` call, or a known/scheduled funding cadence). Given these preconditions, the attack requires only minimal capital (dust deposit) and standard MEV front-running tooling, and is repeatable each time the pool returns to a near-empty state before a reward top-up.

### Recommendation
Do not use raw `balanceOf(operator)` as the reward-distribution denominator without a floor/guard. Options: (1) revert or defer to `queuedRewards` whenever `totalStaked() < some minimum threshold` (not just `== 0`); (2) accrue rewards using a time-weighted/stake-second model instead of an instantaneous balance snapshot at funding time; (3) require a minimum bootstrap stake (burned/locked, similar to ERC4626 dead-share mitigations) before a pool can accept `queueNewRewards`; (4) enforce a minimum vesting/lock period between deposit and reward eligibility for newly deposited stakes so a same-block depositor cannot claim freshly queued rewards.

### Proof of Concept
Foundry test plan:
1. Deploy `MasterMagpie`, a staking `ERC20Mock` token, and register the pool with a `BaseRewardPoolV2` (or `BaseRewardPool`) instance, with a `rewardManager` set as manager.
2. Do not have any pre-existing stakers (`totalStaked() == 0`).
3. As `attacker` (unprivileged EOA), call `masterMagpie.deposit(stakingToken, 1)` so `IERC20(stakingToken).balanceOf(address(masterMagpie)) == 1`.
4. As `manager`, call `rewarder.queueNewRewards(1e18, rewardToken)` (after `rewardToken.approve`/mint to manager) — assert `rewardInfo.rewardPerTokenStored` jumps to `1e18 * 10**stakingDecimals`.
5. As `attacker`, call `masterMagpie` claim path leading to `rewarder.getReward(attacker, attacker)`; assert `rewardToken.balanceOf(attacker)` ≈ `1e18` (full reward), while attacker only ever held 1 wei of stake for a single block — i.e., `earned() >> attacker's fair pro-rata share of historical stake-seconds` (which should be ~0 given zero elapsed time and dust principal).
6. Compare against a control scenario with a genuine large staker present before funding, showing the reward correctly distributes pro-rata, to isolate the dust-supply front-run as the root cause.

### Citations

**File:** rewards/BaseRewardPool.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
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

**File:** rewards/BaseRewardPoolV2.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() public override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L296-313)
```text
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
```

**File:** rewards/MasterMagpie.sol (L334-339)
```text
    /// @notice Deposits staking token to the pool, updates pool and distributes rewards
    /// @param _stakingToken Staking token of the pool
    /// @param _amount Amount to deposit to the pool
    function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _deposit(_stakingToken, msg.sender, _amount, false);
    }
```

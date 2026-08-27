### Title
Front-running reward distribution in `BaseRewardPool`/`BaseRewardPoolV2` allows theft of unclaimed yield from long-term stakers - (File: `rewards/BaseRewardPoolV2.sol`, `rewards/BaseRewardPool.sol`, `wombat/WombatStaking.sol`)

### Summary
`BaseRewardPoolV2`/`BaseRewardPool` distribute rewards by immediately bumping a global `rewardPerTokenStored` accumulator by `_amountReward * 10**decimals / totalStaked()` the instant `queueNewRewards`/`donateRewards` is called, with no time-weighting of how long a staker has actually held their position. This is structurally the same flaw as `Furnace#melt()` in the referenced report: a single lump-sum, discrete reward event that any staked balance at that block captures in full, regardless of stake duration.

### Finding Description
`_provisionReward()` computes the pool's reward-per-share purely from the current `totalStaked()` snapshot at the moment new rewards are pushed in: [1](#0-0) 

`rewardPerToken()` simply returns this stored accumulator (no time-decay, no vesting/streaming of the newly queued amount): [2](#0-1) 

A user's earned amount is `balance * (rewardPerToken - userRewardPerTokenPaid)`, so whoever is staked (via `MasterMagpie`) at the exact moment `queueNewRewards` executes is entitled to their full pro-rata share of the newly added rewards: [3](#0-2) 

`queueNewRewards` on these pools is invoked from `WombatStaking.sol` as part of the periodic bribe/reward harvesting flow, an unprivileged, permissionlessly-triggerable distribution event (the bribe amounts get routed straight into `queueNewRewards` for the relevant rewarder): [4](#0-3) 

Because `balanceOf()` in the rewarder reads live stake from `MasterMagpie.stakingInfo`, and reward accrual for a user is only checkpointed on `deposit`/`withdraw`/`getReward` (via `_updateFor`/`updateReward`), a user can:
1. Watch the mempool/schedule for the next `queueNewRewards` (bribe/harvest distribution) call.
2. Deposit into `MasterMagpie` for the target pool immediately before that transaction.
3. Once `rewardPerTokenStored` jumps, immediately call `getReward`/withdraw to claim their full pro-rata share of the newly queued rewards.

This is the exact reward-sandwich pattern described in the Furnace report (A2 in the summary — front-running a discrete payout event to obtain a share of accrued rewards without contributing time-weighted participation) transplanted onto `BaseRewardPool`'s accounting model.

### Impact Explanation
Long-term stakers who provided liquidity/staked continuously see their share of newly distributed rewards diluted by short-term "flash stakers" who deposit only for the block in which a reward/bribe distribution lands. This constitutes theft of unclaimed yield from legitimate long-term stakers, redirected to opportunistic single-block depositors, without requiring any privileged role — satisfies the "theft or permanent freezing of unclaimed yield" impact bar.

### Likelihood Explanation
`queueNewRewards` calls (via bribe harvesting in `WombatStaking`) happen on a routine, often externally-triggerable/permissionless cadence, and deposit/withdraw into `MasterMagpie` pools carry no cooldown or minimum staking duration enforced at the `BaseRewardPool`/`BaseRewardPoolV2` accounting layer based on the code reviewed. Any wallet capable of front-running a mempool transaction (or predicting the harvest schedule) can execute this repeatedly at low cost (only requiring capital for the duration of one or two blocks), making this a realistically and repeatedly exploitable path, analogous to the confirmed-Medium severity Furnace finding.

### Recommendation
Adopt a time-weighted/streaming reward release for `queueNewRewards`/`donateRewards`, e.g., stream newly queued rewards linearly over a fixed period (as done in `MultiRewarderPerSec.sol`'s `tokenPerSec` streaming model already present elsewhere in the codebase) rather than crediting the entire amount to `rewardPerTokenStored` in a single atomic step. Alternatively, require a minimum bonding/holding period before a staker's balance counts toward `earned()` for freshly queued rewards.

### Proof of Concept
1. Attacker monitors for the next call that will trigger `queueNewRewards` on a `BaseRewardPoolV2` instance (e.g., the periodic bribe/reward harvest processed through `WombatStaking.sol`, lines 397-411).
2. Immediately prior to that transaction, attacker calls `MasterMagpie.deposit(...)` with a large stake for the target pool.
3. The harvest transaction executes `queueNewRewards`, jumping `rewardPerTokenStored` proportional to `totalStaked()` at that moment (`rewards/BaseRewardPoolV2.sol` lines 301-312), which now includes the attacker's freshly deposited balance.
4. Attacker immediately calls `getReward`/`getRewards` to claim their pro-rata share of the newly queued reward (lines 218-235), then withdraws their principal — having captured a full share of the reward for a holding period of essentially zero economic time, diluting genuine long-term stakers.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L145-152)
```text
    function rewardPerToken(address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return rewards[_rewardToken].rewardPerTokenStored;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L290-313)
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
```

**File:** rewards/BaseRewardPoolV2.sol (L316-321)
```text
    function _earned(address _account, address _rewardToken, uint256 _userShare) internal view returns (uint256) {
        return ((_userShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**stakingTokenDecimals) + userRewards[_rewardToken][_account];
    }
```

**File:** wombat/WombatStaking.sol (L397-411)
```text
                        uint256 protocolFee = (rewardAmount * bribeProtocolFee) / DENOMINATOR;

                        if (protocolFee > 0) {
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeFeeCollector, protocolFee);
                        }

                        if (caller != address(0) && bribeCallerFee != 0) {
                            callerFeeAmount = (rewardAmount * bribeCallerFee) / DENOMINATOR;
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeManager, callerFeeAmount);
                        }

                        rewardAmount -= protocolFee;
                        rewardAmount -= callerFeeAmount;
                        IERC20(rewardTokens[i][j]).safeApprove(_rewarders[i], rewardAmount);
                        IBaseRewardPool(_rewarders[i]).queueNewRewards(rewardAmount, address(rewardTokens[i][j]));
```

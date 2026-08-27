### Title
First-staker sniping of accumulated `queuedRewards` in reward pool distribution - ([File: rewards/BaseRewardPoolV2.sol])

### Summary
`_provisionReward` (invoked via `queueNewRewards`/`donateRewards`, and via the sibling `_queueNewRewardsWithoutTransfer` in `DelegateVoteRewardPool.sol`) buffers incoming rewards into `rewardInfo.queuedRewards` whenever `totalStaked() == 0`, and dumps the **entire accumulated backlog** into `rewardPerTokenStored` on the very next provisioning call that happens after `totalStaked() > 0`. Because `stakeFor`/`vote` immediately changes `totalStaked()` before any subsequent reward push, the first staker to appear after a zero-stake period is able to receive the full multi-epoch backlog against a minimal, single-block stake.

### Finding Description
In `rewards/BaseRewardPoolV2.sol` `_provisionReward`: [1](#0-0) 
When `totalStaked() == 0`, incoming reward amounts are only added to `rewardInfo.queuedRewards` and `rewardPerTokenStored` is left untouched — no user accrues anything since there are no stakers to update. Once any staker exists and a further reward push occurs, the whole `queuedRewards` balance is added to `_amountReward` and divided by the *current* `totalStaked()` to update `rewardPerTokenStored`.

The attacker's path is: monitor a `BribeRewardPool` for a pool with `totalStaked() == 0` (`totalSupply == 0` in `rewards/BribeRewardPool.sol`) while `wombatStaking.vote` → `queueNewRewards` repeatedly enqueues rewards into `queuedRewards` via multiple `WombatBribeManager.castVotes()` cycles [2](#0-1) . When the attacker calls `WombatBribeManager.vote()` with a minimal positive delta for that pool, `IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta))` is invoked [3](#0-2) , which sets `totalSupply` to a nonzero value in `BribeRewardPool.stakeFor` [4](#0-3) . The very next `castVotes()`/`donateRewards()` call that forwards bribes for that pool will then compute `rewardPerTokenStored += (_amountReward + queuedRewards) * 10**decimals / totalStaked()` where `totalStaked()` is the attacker's tiny stake — giving the attacker (and any other, even later, stakers) a claim on the whole backlog proportional to their (very small) share versus whoever else stakes afterward at that same reward-per-token checkpoint. Because `userRewardPerTokenPaid` for the attacker is only set at `stakeFor` time (before the flush) via the `updateRewards` modifier's snapshot of `rewardPerToken` *before* the flush occurred, the attacker's `earned()` calculation on the next `getReward`/`unvote(claim=true)` call captures the full flushed delta between their `userRewardPerTokenPaid` and the new `rewardPerTokenStored`, i.e. the entire backlog, since they are the sole staker at flush time.

There is no minimum-stake-duration, no vesting/streaming of queued rewards, and no per-block-of-accrual weighting — the flush is instantaneous and all-or-nothing to whoever holds the stake at the moment of the next non-zero-supply provisioning call. This is a known anti-pattern in Synthetix-style `rewardPerTokenStored` accounting when `totalSupply` is allowed to be transiently zero while rewards keep being pushed externally (via `wombatStaking.vote` bribes, which are protocol-driven and independent of the pool's staked amount).

### Impact Explanation
Direct theft of unclaimed yield: an attacker with a negligible capital outlay (minimal vlMGP vote delta) can claim reward tokens that were economically meant to be distributed pro-rata to whichever LPs/voters eventually stake in that pool, potentially spanning multiple epochs of bribes. This matches "theft of unclaimed yield" and results in permanent loss for whichever legitimate future stakers would otherwise have earned a fair share of that backlog, since once flushed to `rewardPerTokenStored`, the funds are attributable only to the current stake snapshot.

### Likelihood Explanation
Preconditions are realistic and attacker-controlled: any low-interest/new pool with `totalSupply()==0` and pending bribes accumulating (`queuedRewards>0`) is exploitable, requiring only monitoring public on-chain state (`totalStaked()`, `rewards(token).queuedRewards`) and a single `vote()` call with minimal delta plus an `unvote(claim=true)` afterward. No special privileges, flash loans, or reentrancy needed — only unprivileged EOA calls to `vote`/`unvote`. The main capital cost is enough locked vlMGP to place a nonzero vote delta, which can be minimal. This is fully repeatable across any pool that experiences periods of zero stake.

### Recommendation
Do not allow `queuedRewards` to be flushed entirely to the first staker after a zero-supply gap. Options: (1) stream/vest queued rewards over time (e.g., linear release) instead of instant injection into `rewardPerTokenStored`; (2) snapshot a minimum bonding/vesting period before newly staked balances become eligible for pre-existing `queuedRewards`; (3) split queued rewards proportionally by time-weighted stake rather than crediting 100% to the reward-per-token index at the moment of the first stake.

### Proof of Concept
Foundry test plan:
1. Deploy `BribeRewardPool` (or `BaseRewardPoolV2`-based pool) with a reward token, operator = mock `WombatBribeManager`/master.
2. With `totalSupply()==0`, call `queueNewRewards`/`donateRewards` multiple times (simulate several `castVotes` epochs) to accumulate `rewardInfo.queuedRewards` to a large value X.
3. Have attacker call `stakeFor(attacker, 1)` (minimal delta) — confirm `totalSupply` becomes 1, `rewardPerTokenStored` still 0 (no flush yet since stake itself doesn't provision).
4. Call `queueNewRewards`/`donateRewards` with a small additional amount Y (simulating next bribe distribution) — assert `rewardPerTokenStored` jumps by `(X+Y)*1e{decimals}/1`.
5. Call `getReward(attacker,...)` or `withdrawFor(attacker,1,true)` — assert attacker receives `X+Y` reward tokens despite staking only 1 wei-unit for a single block.
6. Assert this equals (or exceeds) the total accumulated backlog, confirming full capture disproportionate to any legitimate time-weighted contribution.

### Citations

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

**File:** wombat/WombatStaking.sol (L755-769)
```text
                    if (!feeInfo.isAddress) {
                        IERC20(rewardToken).safeApprove(feeInfo.to, 0);
                        IERC20(rewardToken).safeApprove(feeInfo.to, feeTosend);
                        IBaseRewardPool(feeInfo.to).queueNewRewards(feeTosend, rewardToken);
                    } else {
                        IERC20(rewardToken).safeTransfer(feeInfo.to, feeTosend);
                        emit RewardPaidTo(feeInfo.to, rewardToken, feeTosend);
                    }
                }
            }
        }

        IERC20(_rewardToken).safeApprove(_rewarder, 0);
        IERC20(_rewardToken).safeApprove(_rewarder, _amount);
        IBaseRewardPool(_rewarder).queueNewRewards(_amount, _rewardToken);
```

**File:** wombat/WombatBribeManager.sol (L196-199)
```text
                if (delta > 0) {
                    pool.totalVoteInVlmgp += uint256(delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] += uint256(delta);
                    IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta));
```

**File:** rewards/BribeRewardPool.sol (L57-67)
```text
    function stakeFor(address _for, uint256 _amount)
        external
        virtual
        onlyOperator
        updateRewards(_for, rewardTokens)
    {
        totalSupply = totalSupply + _amount;
        _balances[_for] = _balances[_for] + _amount;

        emit Staked(_for, _amount);
    }
```

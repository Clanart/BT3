### Title
Vote-then-cast reward front-running lets a voter capture a full epoch's bribes with instantaneous weight - (File: wombat/WombatBribeManager.sol)

### Summary
`vote()` calls `IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta))` which immediately increases the caller's `_balances` in `BribeRewardPool`, and when `voteAndCast()` (or any transaction that calls `vote()` followed by `castVotes()`) subsequently harvests bribes via `wombatStaking.vote()` -> `queueNewRewards`, the reward-per-token calculation divides the freshly harvested bribe by `totalStaked()`, which already includes the balance the attacker staked moments earlier in the same transaction.

### Finding Description
`vote()` at [1](#0-0)  calls `stakeFor` synchronously, crediting the caller's balance in `BribeRewardPool` before any bribe has actually been earned for the current period. `BribeRewardPool.stakeFor` at [2](#0-1)  updates `totalSupply`/`_balances` right after the `updateRewards` modifier snapshots the pre-existing `rewardPerTokenStored`, which correctly prevents the new staker from claiming rewards that were already stored before the stake — that part is safe.

The actual issue is in the ordering of `voteAndCast()` at [3](#0-2) : `vote()` executes first (increasing `totalStaked()`/`_balances[attacker]`), then `castVotes()` executes in the same transaction, which triggers `wombatStaking.vote()` → `voter.vote()` (harvesting the entire pending bribe accrued since the last cast) → `IBaseRewardPool(_rewarders[i]).queueNewRewards(...)` at [4](#0-3) . `queueNewRewards`/`_addRewards` in `BaseRewardPoolV2` computes `rewardPerTokenStored += _amountReward * 1e18 / totalStaked()` at [5](#0-4) , using `totalStaked()` measured *after* the attacker's stake was just added. Because the whole epoch's harvested bribe is distributed pro-rata over the current `totalStaked()` at that single instant, the attacker's freshly-added balance participates fully in a reward pool that accrued over the entire preceding epoch, despite the attacker not having voted for any of that period. This dilutes the share that should have gone to voters who held their position throughout the epoch.

There is no time-weighting/checkpointing mechanism (e.g., a reward-rate-over-time or per-second accrual model) protecting against this — the reward model is a single lump-sum `rewardPerTokenStored` bump per harvest based on instantaneous `totalStaked()`.

### Impact Explanation
An attacker who accumulates `vlMGP` voting power can call `vote()`+`castVotes()` (or `voteAndCast()`) to vote right before a bribe harvest, capturing a pro-rata share of an entire epoch's bribes, then `unvote()` immediately afterward to withdraw their weight without having contributed votes for the period. This constitutes theft of unclaimed yield from long-term voters who did hold their position through the epoch, matching the "theft of unclaimed yield" bucket rather than a full drain of principal.

### Likelihood Explanation
This requires the attacker to hold real `vlMGP`/locked voting power (not flash-loanable within a single transaction, since it derives from actual locked MGP balances tracked by `getUserVotable`), and capital efficiency scales with how large a fraction of a given pool's `totalVoteInVlmgp` the attacker can acquire relative to the honest voters, and how large the pending harvested bribe is at the time `castVotes()` is invoked. This is feasible for any address holding meaningful `vlMGP` and is repeatable every time `castVotes`/`voteAndCast` is called, but is bounded by the actual `vlMGP` capital the attacker must hold (not free/flash-loanable), which limits blast radius compared to a pure logic bug that requires no capital.

Regarding the specific "delegated pool" divergence claim (that `msg.sender != delegatedPool` excludes delegatedPool's vote delta from `totalVlMgpInVote` while still adding it to `pool.totalVoteInVlmgp`, causing `targetVote` in `castVotes` to diverge from `totalVotes()`) — I was not able to fully verify within the tool budget whether this is compensated elsewhere (e.g. `DelegateVoteRewardPool` maintaining its own separate accounting that feeds `totalVlMgpInVote` through another code path). This part of the claim is unconfirmed with the available context.

### Recommendation
Decouple "when a vote is committed" from "when reward accrual happens" for that voter: either (a) require a minimum bonding/cooldown period between `stakeFor` (vote) and eligibility for rewards accrued in bribes harvested in the same block/epoch, (b) checkpoint `rewardPerTokenPaid` for a voter at the time of staking using the *pre-harvest* `rewardPerTokenStored`, but disallow same-transaction vote+cast+unvote combinations (e.g., disallow `unvote` in the same block as `vote`, and disallow `castVotes` benefiting deltas registered in the same block), or (c) move to a continuous per-second reward-rate streaming model instead of a lump-sum harvest-triggered `rewardPerTokenStored` bump, so that time-weighting is enforced structurally rather than by transaction ordering.

### Proof of Concept
Hardhat test plan:
1. Deploy `WombatBribeManager`, `WombatStaking`, `BribeRewardPool`, mock `veWom`/`voter`/`WombatBribe` returning a large pending bribe amount for a target `lp`.
2. Set up two voters: Voter A votes for `lp` with a normal delta well before `castVotes()` is called (simulating a long-term voter who has been staked for the whole epoch).
3. Have Attacker call `voteAndCast([lp], [largeDelta], false)` in a single transaction just before the epoch bribe would otherwise be harvested by someone else, using the same mock harvest amount.
4. Assert: `BribeRewardPool.earned(attacker, rewardToken)` immediately after the transaction is > 0 and proportional to `attackerDelta / (voterA.balance + attackerDelta)` of the entire harvested bribe, despite the attacker's `stakeFor` call having executed in the same transaction as the harvest.
5. Assert that this share is disproportionate to time held (attacker held stake for 0 seconds of the epoch vs. Voter A's full epoch), demonstrating the missing time-weighting invariant.
6. As a follow-up row, have the attacker call `unvote(lp)` in a subsequent transaction and confirm they can withdraw the vote weight while retaining the already-accrued `userRewards[rewardToken][attacker]`, confirming the yield is not clawed back.

### Citations

**File:** wombat/WombatBribeManager.sol (L196-199)
```text
                if (delta > 0) {
                    pool.totalVoteInVlmgp += uint256(delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] += uint256(delta);
                    IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta));
```

**File:** wombat/WombatBribeManager.sol (L315-322)
```text
    function voteAndCast(
        address[] calldata _lps,
        int256[] calldata _deltas,
        bool swapForBnb
    ) external returns (address[][] memory finalRewardTokens, uint256[][] memory finalFeeAmounts) {
        vote(_lps, _deltas);
        (finalRewardTokens, finalFeeAmounts) = castVotes(swapForBnb);
    }
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

**File:** wombat/WombatStaking.sol (L403-411)
```text
                        if (caller != address(0) && bribeCallerFee != 0) {
                            callerFeeAmount = (rewardAmount * bribeCallerFee) / DENOMINATOR;
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeManager, callerFeeAmount);
                        }

                        rewardAmount -= protocolFee;
                        rewardAmount -= callerFeeAmount;
                        IERC20(rewardTokens[i][j]).safeApprove(_rewarders[i], rewardAmount);
                        IBaseRewardPool(_rewarders[i]).queueNewRewards(rewardAmount, address(rewardTokens[i][j]));
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

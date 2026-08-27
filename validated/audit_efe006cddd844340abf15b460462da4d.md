### Title
Just-in-time vote/stake before reward provisioning dilutes/steals unclaimed bribe yield from existing voters - ([File: rewards/BaseRewardPoolV2.sol], [File: wombat/WombatBribeManager.sol])

### Summary
`BaseRewardPoolV2._provisionReward` (called from `queueNewRewards`) distributes rewards as an instantaneous lump-sum divided by `totalStaked()` at the moment of the call, with no time-weighting. Since `WombatBribeManager.vote()` immediately calls `stakeFor` on the rewarder, and `castVotes()` is a public, unrestricted function that triggers `queueNewRewards`, any vlMGP holder can inflate their staked share in a target pool's rewarder in the same block right before a `castVotes()` call, capturing a proportional slice of that reward batch despite holding the position for zero elapsed time, then immediately unvote/withdraw.

### Finding Description
`_provisionReward` credits new rewards as: [1](#0-0) 
`rewardInfo.rewardPerTokenStored` is bumped by `_amountReward * 10**decimals / totalStaked()`, using `totalStaked()` measured at the exact moment `queueNewRewards` executes — there is no per-second/streaming accrual and no minimum holding period.

`WombatBribeManager.vote()` lets any vlMGP holder immediately increase their staked balance in a pool's `BribeRewardPool` by calling `stakeFor`: [2](#0-1) 

`castVotes()` is `public`, callable by anyone with no access control or cooldown, and it triggers `wombatStaking.vote(...)` which harvests bribes and forwards them via `queueNewRewards`: [3](#0-2) 

Exploit flow:
1. Attacker holds/acquires vlMGP votable power (not flash-loanable, but no lock-up period is enforced against reallocating votes between pools).
2. Attacker calls `vote([targetLp], [+largeDelta])`, which calls `IBribeRewardPool(rewarder).stakeFor(attacker, largeDelta)`, instantly increasing `totalStaked()` for that rewarder in the modifier `updateRewards`, which correctly checkpoints the attacker at the pre-injection `rewardPerTokenStored` (this checkpoint is *not* the bug — the equality-skip optimization in `updateRewards`/`_updateFor` is mathematically safe since no reward index change means no reward change to compute). [4](#0-3) 
3. In the same block, attacker (or anyone) calls `castVotes()`, which harvests external bribe rewards and calls `queueNewRewards` → `_provisionReward`, which divides the harvested reward by the now-inflated `totalStaked()` (including attacker's just-added stake) and increases `rewardPerTokenStored`.
4. Attacker calls `claimBribe`/`getReward` to realize `_earned = userShare * (rewardPerToken - userRewardPerTokenPaid)`, capturing a share proportional to their large balance despite zero elapsed time contributing to that yield.
5. Attacker calls `unvote`/`withdrawFor` to remove the position immediately after, with `withdrawFor` also gated only by `updateRewards`, imposing no penalty or cooldown: [5](#0-4) 

No modifier, `nonReentrant`, or index update in this call chain distinguishes "long-held voters" from "same-block just-in-time voters" — the reward-per-token checkpoint model treats all current balance holders identically regardless of duration held.

### Impact Explanation
This is theft/dilution of unclaimed yield (bribe rewards) from genuine long-term voters, matching the Immunefi "theft of unclaimed yield" impact class. Every honest voter who has accrued a share of the pending bribe harvest has their pro-rata claim diluted by the attacker's momentary stake injection, and the attacker extracts value they did not economically earn.

### Likelihood Explanation
Feasible and repeatable each time a `castVotes()` call is imminent (predictable, since anyone can trigger it, including the attacker themselves in the same transaction via `voteAndCast`). Requires the attacker to hold or temporarily redirect a large vlMGP voting allocation (`getUserVotable` cap applies), which is real economic capital, not free — but no lock-up prevents reallocating already-locked vlMGP into a target pool for one block and back. `voteAndCast(_lps, _deltas, swapForBnb)` even packages steps 2–3 into a single atomic call: [6](#0-5) 

### Recommendation
Introduce time-weighted reward accrual (streaming reward rate over a duration, à la Synthetix `StakingRewards` with `rewardRate`/`periodFinish`) instead of instantaneous lump-sum crediting proportional to `totalStaked()` at the call instant. Alternatively, snapshot eligible balances prior to the block in which `queueNewRewards` executes (e.g., checkpoint balances at the start of each voting epoch) so that same-block stake changes cannot affect that epoch's distribution, and/or enforce a minimum holding/cooldown period between `vote()`/`stakeFor` and reward eligibility.

### Proof of Concept
Foundry test plan:
1. Deploy `BribeRewardPool` and `WombatBribeManager` fixtures with a reward token and two voters: `honestVoter` (staked `X` vlMGP for many blocks) and `attacker` (holds unused votable vlMGP).
2. Advance blocks/time so pending external bribe rewards accumulate for `targetLp`.
3. In a single block: `attacker.vote([targetLp], [+largeDelta])` then `attacker.castVotes(false)` (or `voteAndCast`), with `wombatStaking.vote` mocked/routed to call `queueNewRewards(bribeAmount, rewardToken)` on the rewarder.
4. Assert `attacker.earned(rewardToken)` after step 3 is `> 0` and proportional to `largeDelta / (largeDelta + honestVoterStake)`, despite `attacker` having staked 0 blocks/seconds before the harvest.
5. Assert `attacker.unvote(targetLp)` succeeds immediately after claiming, with no penalty, and that `honestVoter.earned(rewardToken)` is strictly less than it would have been had the attacker not injected `largeDelta` (i.e., total distributed reward pool is fixed, honest voter's share is diluted).
6. Compare against baseline (no attacker injection) to quantify the diluted amount = concrete loss for `honestVoter`.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L107-120)
```text
    modifier updateRewards(address _account, address[] memory _rewards) {
        uint256 length = _rewards.length;
        uint256 userShare = balanceOf(_account);
        
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewards[index];
            // if a reward stopped queuing, no need to recalculate to save gas fee
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;
            userRewards[rewardToken][_account] = _earned(_account, rewardToken, userShare);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
        _;
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

**File:** wombat/WombatBribeManager.sol (L189-205)
```text
        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[_lps[i]];
            if (!pool.isActive)
                revert PoolNotActive();
            int256 delta = _deltas[i];
            totalUserVote += delta;
            if (delta != 0) {
                if (delta > 0) {
                    pool.totalVoteInVlmgp += uint256(delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] += uint256(delta);
                    IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta));
                } else {
                    pool.totalVoteInVlmgp -= uint256(-delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] -= uint256(-delta);
                    IBribeRewardPool(pool.rewarder).withdrawFor(msg.sender, uint256(-delta), false);
                }
            }
```

**File:** wombat/WombatBribeManager.sol (L241-276)
```text
    function castVotes(bool swapForBnb)
        override public
        returns (address[][] memory finalRewardTokens, uint256[][] memory finalFeeAmounts)
    {
        lastCastTime = block.timestamp;
        uint256 length = pools.length;
        address[] memory _pools = new address[](length);
        int256[] memory votes = new int256[](length);
        address[] memory rewarders = new address[](length);

        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[pools[i]];
            _pools[i] = pool.poolAddress;
            rewarders[i] = pool.rewarder;

            uint256 currentVote = getVoteForLp(pool.poolAddress);
            uint256 targetVoteInLMGP = pool.totalVoteInVlmgp;
            uint256 targetVote = 0;

            if (totalVlMgpInVote != 0) {
                targetVote = targetVoteInLMGP * totalVotes() / totalVlMgpInVote;
            }

            if (targetVote >= currentVote) {
                votes[i] = int256(targetVote - currentVote);
            } else {
                votes[i] = int256(targetVote) - int256(currentVote);
            }
        }

        (address[][] memory rewardTokens, uint256[][] memory feeAmounts) = wombatStaking.vote(
            _pools,
            votes,
            rewarders,
            msg.sender
        );
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

**File:** rewards/BribeRewardPool.sol (L72-85)
```text
    function withdrawFor(
        address _for,
        uint256 _amount,
        bool claim
    ) external virtual onlyOperator updateRewards(_for, rewardTokens) {
        totalSupply = totalSupply - _amount;
        _balances[_for] = _balances[_for] - _amount;

        emit Withdrawn(_for, _amount);

        if (claim) {
            _getReward(_for);
        }
    }
```

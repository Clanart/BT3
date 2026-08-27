### Title
Just-in-time vote reallocation via `vote()` before `castVotes()` steals pro-rata share of freshly harvested bribes from long-term voters - (File: `wombat/WombatBribeManager.sol`)

### Summary
`WombatBribeManager.vote()` lets any vlMGP holder freely reallocate voting power between pools with **zero net capital cost** (positive delta on target pool, negative delta on another pool). `castVotes()` is permissionless and, when executed, harvests bribes and pushes them into each pool's `BribeRewardPool` via a lump-sum `rewardPerTokenStored` update that uses the pool's `totalStaked()` **at the moment of harvest**. Because reward accrual has no time-weighting/vesting, an attacker can shift votes into a target pool immediately before someone (anyone) calls `castVotes()`, capture a full pro-rata share of the entire pending bribe accumulated by long-term voters, then shift the votes back out afterward.

### Finding Description
`vote()` updates `pool.totalVoteInVlmgp`, `userVotedForPoolInVlmgp`, and calls `IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta))` for positive deltas, and `withdrawFor(...)` for negative ones [1](#0-0) . Crucially, an attacker can pass both a negative delta for a pool they currently vote for and a positive delta for the target pool in the same call, so `userTotalVotedInVlmgp` (and thus the `NotEnoughVote` check) is unaffected — no new vlMGP needs to be locked [2](#0-1) .

`BribeRewardPool.stakeFor` is guarded by `updateRewards(_for, rewardTokens)`, which snapshots `userRewardPerTokenPaid[_account] = rewardPerToken()` **before** `totalSupply`/`_balances[_for]` are increased [3](#0-2) [4](#0-3) . This means the newly added stake is excluded from *past* accrued rewards but is fully counted for *any reward added after this point*.

`castVotes()` is a public, permissionless function that anyone can call to harvest bribes [5](#0-4) . When bribes are harvested and forwarded to `_forwardRewards`, the reward pool's `_provisionReward` computes:
```
rewardPerTokenStored += (_amountReward * 10**decimals) / totalStaked();
```
using `totalStaked()` at that exact block [6](#0-5) . Because this is a single global lump-sum accumulator with no minimum holding period, any account whose stake is present at that instant is entitled to `_userShare * (new rewardPerToken − paid rewardPerToken)`, i.e., a full pro-rata slice of a bribe that accrued over the whole prior period, even though the attacker only held that stake for one block [7](#0-6) .

No existing check prevents this: `vote()` has no cooldown, minimum holding duration, or time-weighted checkpointing tied to `castVotes()` harvest timing, and `castVotes()` itself performs no snapshot-based reward distribution — it relies entirely on the reward pool's instantaneous `totalStaked()`.

### Impact Explanation
This is theft of unclaimed yield: legitimate long-term voters for a pool have their bribe reward diluted by an attacker who joins the pool's vote for a single block right before harvest and exits immediately after, without incurring the opportunity cost of holding that vote position for the full accrual period. This matches Immunefi's "theft of unclaimed yield" / reward-share manipulation impact class. The magnitude scales with the size of the pending, unharvested bribe and the fraction of total vote weight the attacker can redirect in one transaction.

### Likelihood Explanation
- No flash loan or new capital is required: an attacker who already holds vlMGP votes anywhere in the system can reallocate them for free via `vote()` (net-zero delta across pools).
- `castVotes()` is public/permissionless, so anyone (including the attacker) can trigger the harvest immediately after positioning votes, or the attacker can simply front-run someone else's pending `castVotes()` transaction (mempool visibility on EVM chains like BNB Chain makes this straightforward).
- The attack is fully repeatable every time bribes accumulate and a harvest is pending, and requires only holding some existing vlMGP lock (a normal precondition for any voter, not owner/governance/operator privilege).

### Recommendation
Introduce time-weighted vote accounting for bribe distribution, e.g.:
- Require a minimum holding/lock period after `vote()` before a user's stake counts toward the next `castVotes()` harvest (e.g., checkpoint votes at the start of an epoch and only distribute rewards to voters whose position existed before the epoch began).
- Alternatively, stream rewards over time (e.g., via a reward-rate/duration model like Synthetix `StakingRewards` `notifyRewardAmount` with a `rewardsDuration`) instead of instantaneous lump-sum `rewardPerTokenStored` bumps, so newly joined stakers only accrue rewards proportional to time held.
- Consider adding a cooldown on `vote()`/`unvote()` relative to `lastCastTime` so votes cannot be reallocated in the same block/epoch as a harvest.

### Proof of Concept
Foundry test plan:
1. Deploy `WombatBribeManager`, `BribeRewardPool` (or mock), `VLMGP`, and a mock `WombatStaking`/`voter` that returns a fixed bribe amount on `wombatStaking.vote(...)`.
2. Set up two users: `alice` (long-term voter) locks vlMGP and calls `vote()` for `poolA` at epoch start; `bob` (attacker) locks/holds vlMGP votes for `poolB` at epoch start.
3. Let time pass (simulate an epoch of accrued bribes owed to `poolA`, e.g., mock the staking contract to report pending bribe as growing with time).
4. Immediately before calling `castVotes(false)`, have `bob` call `vote([poolB, poolA], [-fullBobVotes, +fullBobVotes])` to move 100% of his existing vote weight into `poolA` in the same block.
5. Call `castVotes(false)` (by any caller) to harvest the bribe into `poolA`'s `BribeRewardPool` via `queueNewRewards`.
6. Assert: `IBribeRewardPool(poolA.rewarder).earned(bob, rewardToken)` is non-trivial (roughly `bobShare/totalPoolAShare * harvestedAmount`) despite `bob` having voted for `poolA` for only one block, while `alice.earned(...)` is diluted below what it would have been had `bob` not joined (`aliceShare_without_bob/totalShare_without_bob * harvestedAmount`).
7. Immediately after, have `bob` call `vote()` again to move his votes back out of `poolA`, showing round-trip with no net capital cost or lock-up penalty. [8](#0-7) [3](#0-2) [9](#0-8)

### Citations

**File:** wombat/WombatBribeManager.sol (L182-220)
```text
    function vote(address[] calldata _lps, int256[] calldata _deltas) override public {
        if (_lps.length != _deltas.length)
            revert LengthMismatch();

        uint256 length = _lps.length;
        int256 totalUserVote;

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
        }

        if (msg.sender != delegatedPool) {
            if (totalUserVote > 0) {
                userTotalVotedInVlmgp[msg.sender] += uint256(totalUserVote);
                totalVlMgpInVote += uint256(totalUserVote);
            } else {
                userTotalVotedInVlmgp[msg.sender] -= uint256(-totalUserVote);
                totalVlMgpInVote -= uint256(-totalUserVote);
            }
        }

        if (userTotalVotedInVlmgp[msg.sender] > getUserVotable(msg.sender))
            revert NotEnoughVote();
    }
```

**File:** wombat/WombatBribeManager.sol (L241-296)
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

        // comment outs for now since chainlink fails sometimes
        // if (swapForBnb) {
        //     finalFeeAmounts = new uint256[][](1);
        //     finalFeeAmounts[0] = new uint256[](1);
        //     finalFeeAmounts[0][0] = _swapFeesForBnb(rewardTokens, feeAmounts);
        //     finalRewardTokens = new address[][](1);
        //     finalRewardTokens[0] = new address[](1);
        //     finalRewardTokens[0][0] = address(0);
        // } else {
            _forwardRewards(rewardTokens, feeAmounts);
            finalRewardTokens = rewardTokens;
            finalFeeAmounts = feeAmounts;
        // }

        // send rewards to the delegate pool
        if (delegatedPool != address(0)) IDelegateVoteRewardPool(delegatedPool).harvestAll();

        emit VoteCasted(msg.sender, lastCastTime);
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

**File:** rewards/BaseRewardPoolV2.sol (L290-321)
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

    function _earned(address _account, address _rewardToken, uint256 _userShare) internal view returns (uint256) {
        return ((_userShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**stakingTokenDecimals) + userRewards[_rewardToken][_account];
    }
```

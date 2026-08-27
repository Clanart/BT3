### Title
Flash-vote reward sniping via instant (non-time-weighted) `rewardPerTokenStored` bump in `BribeRewardPool`/`BaseRewardPoolV2` combined with atomic `voteAndCast` - ([File: wombat/WombatBribeManager.sol, rewards/BaseRewardPoolV2.sol, rewards/BribeRewardPool.sol])

### Summary
`WombatBribeManager.vote()` immediately calls `IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, delta)` which sets the caller's `userRewardPerTokenPaid` checkpoint to the *current* `rewardPerTokenStored` value [1](#0-0)  before any bribe harvest has bumped that value. When `castVotes()` (or the atomic `voteAndCast`) subsequently triggers `wombatStaking.vote()` and forwards harvested bribes into the rewarder via reward provisioning, `rewardPerTokenStored` jumps instantly and proportionally to `totalStaked()` at that single block [2](#0-1) . Because the attacker's checkpoint was set *before* this jump, their next `earned()`/`getReward()` call captures the entire per-token delta as if they had been staked for the whole accrual period, despite having contributed zero real, time-weighted Wombat vote-weight.

### Finding Description
The reward accounting in `BaseRewardPoolV2` is a MasterChef-style instantaneous index update, not a linear/streamed distribution: `_provisionReward()` adds `_amountReward * 1e18 / totalStaked()` to `rewardPerTokenStored` in a single step [3](#0-2) . `earned()`/`_earned()` compute a user's reward strictly as `balance * (rewardPerToken - userRewardPerTokenPaid)` [4](#0-3)  — it has no notion of *when* the user staked relative to *when* the reward was earned by the underlying gauge, only whether their checkpoint predates the index bump.

`stakeFor`/`withdrawFor` in `BribeRewardPool` are wrapped with the `updateRewards` modifier, which runs *before* the function body executes and sets `userRewardPerTokenPaid[_for] = rewardPerToken()` at the pre-stake value [5](#0-4) [6](#0-5) .

Exploit flow:
1. Attacker holds vlMGP and calls `vote(lpX, +L)`. This immediately calls `stakeFor(attacker, L)`, checkpointing `userRewardPerTokenPaid` at the pre-harvest value and inflating `pool.totalVoteInVlmgp` and `totalSupply` in the rewarder [7](#0-6) .
2. In the same transaction (via `voteAndCast`, which composes `vote()` then `castVotes()` atomically [8](#0-7) ) or in the very next block, `castVotes()` calls `wombatStaking.vote(...)` to harvest real Wombat bribes accrued by genuine, previously-cast, time-weighted votes, then `_forwardRewards()` pushes these bribes into the rewarder, bumping `rewardPerTokenStored` for lpX's `BribeRewardPool` based on `totalStaked()` which now includes the attacker's freshly staked, zero-duration balance [9](#0-8) .
3. Because the attacker's checkpoint predates this bump, their subsequent `claimBribe`/`claimAllBribes` call returns a proportional share of the entire harvested bribe pot, even though the underlying Wombat gauge vote-weight that generated those bribes reflects long-term voters' locked/time-weighted contributions, not the attacker's instantaneous presence.
4. The attacker then calls `unvote(lpX)` immediately, which calls `withdrawFor(..., claim=true)` to both exit the position and realize the reward in one step [10](#0-9) , restoring their vlMGP votable capacity for the next pool/harvest cycle.
5. This is repeatable across pools and harvest cycles with the same capital, since `unvote` fully frees `userTotalVotedInVlmgp` for reuse.

No cooldown, minimum-holding-period, or streaming/vesting mechanism exists to prevent a staker from being counted in `totalStaked()` only at the exact block a reward is queued. `nonReentrant`/`whenNotPaused` modifiers do not address this, since the exploit does not rely on reentrancy or pausing — it relies purely on the order of operations between `vote()`'s stake-checkpoint and `castVotes()`'s reward injection.

### Impact Explanation
This is a theft-of-unclaimed-yield vulnerability: bribe rewards harvested from Wombat's gauge (funded by genuine, time-weighted voter commitment) get redistributed to an attacker who contributed zero real duration of gauge weight, diluting the share ultimately claimable by long-term voters who kept their vlMGP allocated to that pool across the entire accrual period. This matches the "theft of unclaimed yield" impact class. The loss is bounded by the size of the harvested bribe batch and the attacker's relative stake at harvest time versus genuine long-term stakers.

### Likelihood Explanation
- Requires only vlMGP already locked by the attacker (no privileged role) — preconditions match the "unprivileged attacker" model exactly.
- `castVotes()` and `voteAndCast()` are both public, callable by anyone; an attacker can either front-run an imminent `castVotes()` call by watching the mempool, or self-trigger the harvest atomically via `voteAndCast()` right after voting, guaranteeing same-block execution and eliminating front-running risk entirely.
- Fully repeatable across every pool/epoch and does not require unlocking vlMGP — attacker toggles `vote`/`unvote` between pools before/after each harvest.
- Capital requirement is only the vlMGP needed to move `pool.totalVoteInVlmgp`/rewarder `totalSupply` meaningfully relative to other stakers; larger delta yields proportionally larger share of harvested bribes.

### Recommendation
Decouple reward eligibility from instantaneous balance at harvest time:
- Introduce a minimum staking duration/cooldown before newly staked vote-weight in `BribeRewardPool` becomes eligible for pending reward distributions (e.g., snapshot balances at the start of each epoch and only reward against pre-epoch balances), or
- Stream/vest queued rewards linearly over a period (similar to a `rewardRate`/`periodFinish` design) rather than applying them as an instantaneous `rewardPerTokenStored` bump, so that stakers only accrue reward proportional to actual time held during the distribution window, or
- Require `vote()`'s stake registration to only take effect for the *next* `castVotes()` cycle (i.e., queue the delta and apply it after the next harvest, not before), so a same-epoch flash vote+unvote cannot retroactively capture bribes harvested from previously-cast (unrelated) real votes.

### Proof of Concept
Foundry test plan (`WombatBribeManager`/`BribeRewardPool` integration test):
1. Deploy `WombatBribeManager`, `WombatStaking` (mocked to return a fixed pending bribe amount on `vote()`), and a `BribeRewardPool` for `lpX`.
2. Have `voterA` (long-term holder) call `vote(lpX, +100e18)` and leave it staked.
3. Advance time / mock accrual so that a pending bribe of `1000` reward tokens exists for `lpX` in the mocked `wombatStaking`.
4. Right before calling `castVotes()`, have `attacker` call `voteAndCast([lpX], [+900e18], false)` in a single transaction (or front-run `castVotes()` in the same block).
5. Assert: `attacker.earned(lpX_rewarder, rewardToken)` immediately after this transaction is > 0 and proportional to `900/(900+100)` of the `1000` harvested reward, despite `attacker` having zero prior accrual period.
6. Have `attacker` call `unvote(lpX)` in the same or next block, successfully withdrawing both principal vote-weight and the claimed bribe share.
7. Compare `voterA`'s realized share (`100/(900+100)` fraction) against the "fair" expectation of receiving the full `1000` reward tokens (since `voterA` was the only genuine time-weighted contributor for the entire accrual period) — demonstrating the dilution.

### Citations

**File:** wombat/WombatBribeManager.sol (L189-206)
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
        }
```

**File:** wombat/WombatBribeManager.sol (L223-237)
```text
    function unvote(address _lp) public {
        Pool storage pool = poolInfos[_lp];
        uint256 currentVote = userVotedForPoolInVlmgp[msg.sender][pool.poolAddress];
        if(!pool.isActive)
            revert PoolNotActive();
        
        pool.totalVoteInVlmgp -= uint256(currentVote);
        userTotalVotedInVlmgp[msg.sender] -= uint256(currentVote);
        userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] = 0;
        if (msg.sender != delegatedPool) {
            totalVlMgpInVote -= currentVote;
        }
        
        IBribeRewardPool(pool.rewarder).withdrawFor(msg.sender, uint256(currentVote), true);
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

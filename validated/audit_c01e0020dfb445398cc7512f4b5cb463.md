### Title
Delegate reward sniping via permissionless-timed `harvestAll()` triggered from `castVotes()` lets a staker capture disproportionate delegate rewards - (File: wombat/WombatBribeManager.sol / rewards/DelegateVoteRewardPool.sol)

### Summary
`castVotes()` is a fully public function that unconditionally calls `IDelegateVoteRewardPool(delegatedPool).harvestAll()` at its end [1](#0-0) , and `DelegateVoteRewardPool.harvestAll()` itself is `external` with no access-control modifier at all [2](#0-1) . Combined with the fact that new rewards are injected into a single global `rewardPerTokenStored` accumulator that is applied prospectively based only on instantaneous `totalSupply`/balance [3](#0-2) , an attacker can stake a large delegated-vote position immediately before triggering (or front-running) a harvest, claim a disproportionate share of the freshly harvested bribes, then immediately unstake — capturing yield that should have accrued to long-term delegators.

### Finding Description
`vote()` lets any vlMGP holder stake into the `delegatedPool`'s rewarder via `IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, delta)` [4](#0-3) , and `unvote()`/negative `vote()` deltas let them withdraw with no cooldown or vesting period enforced in this contract [5](#0-4) .

`DelegateVoteRewardPool.harvestAll()` pulls the underlying bribes that have accrued to the delegate's on-chain positions and queues them into the pool's reward accounting via `_manageRewards` → `_queueNewRewardsWithoutTransfer`, which increments a single `rewardPerTokenStored` scaled by `1/totalSupply` at the moment of the call [6](#0-5) . Because `earned()` (inherited from `BaseRewardPoolV2`) is computed from the delta between the current `rewardPerTokenStored` and the user's last-recorded checkpoint, a staker's share of any newly-added reward batch depends only on their balance at the moment the batch is queued — not on how long that balance had actually been staked. `updateRewards` snapshots this checkpoint on every `stakeFor`/`withdrawFor`/`getReward` call but does not enforce any minimum holding period.

`harvestAll()` has no `onlyOperator` or any other access restriction, unlike `stakeFor`/`withdrawFor`/`getReward`, so any address can call it directly at the exact block they choose, and separately, `castVotes()` is `public` with no restrictions and always ends by calling `harvestAll()` [7](#0-6) . This gives an attacker full control over exactly when a batch of pending bribes gets crystallized into the reward-per-token accumulator relative to their own stake size in `DelegateVoteRewardPool`.

Exploit flow:
1. Attacker locks vlMGP and monitors pending bribes accrued to `delegatedPool`'s underlying vote positions (via `previewBribes`/on-chain state).
2. Right before calling `harvestAll()` (or `castVotes(false)`), attacker calls `vote([delegatedPool], [+largeDelta])`, sharply increasing their share of `totalSupply` in `DelegateVoteRewardPool`.
3. Attacker calls `harvestAll()` (or `castVotes(false)`), crystallizing the pending bribes into `rewardPerTokenStored` while the attacker holds an inflated share of `totalSupply`.
4. Attacker calls `getReward(attacker)` to claim their disproportionate share.
5. Attacker calls `vote([delegatedPool], [-largeDelta])` to unstake, having captured yield that accrued while other users' stake was the majority of `totalSupply` over time.

Existing mechanisms do not prevent this: there is no `nonReentrant` issue here (rewards aren't double-counted), no cooldown/lock on delegated stake changes, and the reward-per-token model is inherently non-time-weighted, so the "existing checks" (the `updateRewards` modifier, `onlyOperator` on `stakeFor`/`withdrawFor`) do not stop an attacker from timing their stake size around harvest events they themselves can trigger.

### Impact Explanation
This is theft of unclaimed yield: long-term delegators who kept capital staked in `DelegateVoteRewardPool` across the full accrual period have their proportional share of harvested bribes diluted by an attacker who stakes only momentarily around the harvest. This matches the Immunefi impact class "theft of unclaimed yield" via harvest-timing manipulation. The scoped impact is bounded by the size of pending bribes at harvest time and the attacker's ability to size their stake relative to existing `totalSupply`, but is real economic loss extracted from other delegators without proportional risk/time exposure.

### Likelihood Explanation
Preconditions are modest: the attacker only needs vlMGP (obtainable by anyone, no privileged role) and the ability to call public functions (`vote`, `harvestAll`/`castVotes`, `getReward`) — all unprivileged. The attack is repeatable every time a meaningful batch of bribes is pending, and capital need only be held transiently (deposit → harvest → claim → withdraw), constrained only by whatever underlying lock mechanics `vlMGP` itself imposes on vote-weight adjustments (not fully covered by this contract). Because `harvestAll()` has zero access control, timing precision is high — the attacker does not need to race other callers of `castVotes()`, they can call `harvestAll()` themselves at the exact block they want.

### Recommendation
- Add access control to `DelegateVoteRewardPool.harvestAll()` (e.g., restrict to `operator`/`onlyOperator` or a keeper role) so an untrusted party cannot dictate the exact block of reward crystallization.
- Introduce a minimum holding period / time-weighted-average-balance mechanism for `DelegateVoteRewardPool` stakers (e.g., checkpoint-based accrual, or require rewards to vest linearly rather than being instantly claimable against the latest `rewardPerTokenStored`), or apply a cooldown on `vote`/`unvote` changes to `delegatedPool` stake to prevent rapid stake-in/harvest/claim/stake-out cycles.

### Proof of Concept
Foundry test plan:
1. Deploy `WombatBribeManager`, `DelegateVoteRewardPool`, mock `vlMGP`, mock bribe pools with a mock bribe token that accrues rewards over time to the `delegatedPool`'s underlying votes.
2. Set up two delegators: "Honest" (stakes early, holds through several harvests) and "Attacker" (stakes only immediately before a harvest each cycle).
3. Scenario A (honest timing): both users vote for `delegatedPool` at t0 with equal vlMGP, wait N blocks/bribe-accrual, call `castVotes(false)` once, both call `getReward`. Assert each receives ~50% of harvested rewards (time-weighted proportional split).
4. Scenario B (manipulated timing): Honest user votes at t0 and holds; Attacker waits until right before a large pending bribe batch is ready, then calls `vote([delegatedPool], +X)` with `X` equal to Honest's balance (doubling `totalSupply`), immediately calls `harvestAll()` (or `castVotes(false)`), then `getReward(attacker)`, then `vote([delegatedPool], -X)` to exit.
5. Assert that in Scenario B, Attacker's realized reward share for that harvest batch (~50%) is disproportionate to their time-weighted stake contribution (near 0%, since they held for a negligible fraction of the accrual period), violating the invariant `claimedReward ≈ Σ(balance_i(t) dt) / Σ(totalSupply(t) dt)`.
6. Assert Honest user's realized share for that same batch drops from ~100% (their time-weighted fair share) to ~50%, quantifying the diluted/stolen yield. [7](#0-6) [8](#0-7)

### Citations

**File:** wombat/WombatBribeManager.sol (L195-205)
```text
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

**File:** rewards/DelegateVoteRewardPool.sol (L57-103)
```text
    function stakeFor(
        address _for,
        uint256 _amount
    ) external override onlyOperator updateRewards(_for, rewardTokens) {
        totalSupply = totalSupply + _amount;
        _balances[_for] = _balances[_for] + _amount;
        _updateVote();

        emit Staked(_for, _amount);
    }

    function withdrawFor(
        address _for,
        uint256 _amount,
        bool _claim
    ) external override onlyOperator updateRewards(_for, rewardTokens) {
        totalSupply = totalSupply - _amount;
        _balances[_for] = _balances[_for] - _amount;
        _updateVote();

        emit Withdrawn(_for, _amount);

        if (_claim) {
            _getReward(_for);
        }
    }

    function getReward(
        address _for
    )
        public
        updateRewards(_for, rewardTokens)
        returns (
            address[] memory rewardTokensList,
            uint256[] memory earnedRewards
        )
    {
        (rewardTokensList, earnedRewards) = _getDelegateReward(_for);
    }

    function harvestAll() external {
        (
            address[] memory rewardTokensList,
            uint256[] memory earnedRewards
        ) = IWombatBribeManager(operator).claimAllBribes(address(this));
        _manageRewards(rewardTokensList, earnedRewards);
    }
```

**File:** rewards/DelegateVoteRewardPool.sol (L178-203)
```text
    function _queueNewRewardsWithoutTransfer(
        uint256 _amountReward,
        address _rewardToken
    ) internal {
        if (!isRewardToken[_rewardToken]) {
            rewardTokens.push(_rewardToken);
            isRewardToken[_rewardToken] = true;
        }
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;
        if (totalSupply == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10 ** this.stakingDecimals()) /
                totalSupply;
        }
        emit RewardAdded(_amountReward, _rewardToken);
    }
```

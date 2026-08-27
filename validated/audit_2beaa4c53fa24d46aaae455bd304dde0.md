### Title
Flash-vote sandwich attack allows theft of bribe rewards via instantaneous (non time-weighted) reward accounting in `BribeRewardPool` - ([File: wombat/WombatBribeManager.sol], [File: rewards/BribeRewardPool.sol], [File: rewards/BaseRewardPoolV2.sol])

### Summary
`WombatBribeManager.vote()`/`unvote()` immediately mutate `IBribeRewardPool.stakeFor`/`withdrawFor` balances that are used purely as reward-splitting weights, while `_provisionReward` (called from `castVotes()` → `wombatStaking.vote()` → `queueNewRewards()`) distributes newly harvested bribes as a single atomic snapshot proportional to `totalStaked()` at that instant, with no time-weighting (`rewardPerTokenStored += amount * 1e18 / totalStaked()`). An attacker can `vote()` immediately before another user's `castVotes()` call and `unvote()` immediately after, in the same block, to grab a share of bribes that were actually earned by other users' pre-existing on-chain vote weight over time.

### Finding Description
`WombatBribeManager.vote()` calls `IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta))` synchronously, increasing `totalSupply`/`_balances[msg.sender]` in `BribeRewardPool` [1](#0-0) . This happens without any corresponding on-chain veWOM reallocation — that only occurs later when someone calls `castVotes()`, which computes deltas from `pool.totalVoteInVlmgp` and forwards them to `wombatStaking.vote()` → `voter.vote()` → `IBaseRewardPool.queueNewRewards()` [2](#0-1) [3](#0-2) .

The harvested bribe amount returned by `voter.vote()` reflects rewards accrued by the *previous* on-chain vote weight over elapsed time (i.e., earned by prior voters), not by whatever `_deltas` are submitted in the same call. However, `queueNewRewards()` → `_provisionReward()` distributes that harvested amount as an instantaneous, non-time-weighted increment: `rewardInfo.rewardPerTokenStored += (_amountReward * 1e18) / totalStaked()` [4](#0-3) . Any address whose `_balances` was inflated via `stakeFor` moments earlier — with `userRewardPerTokenPaid` still checkpointed at the pre-harvest value via the `updateRewards` modifier — captures its pro-rata share of the entire harvest the instant `rewardPerTokenStored` jumps, diluting the share earned by legitimate long-term voters [5](#0-4) .

Exploit flow (single block / MEV bundle):
1. Attacker calls `vote()` with a large positive delta on a pool about to be harvested, right before a pending `castVotes()` transaction. This calls `stakeFor`, checkpointing `userRewardPerTokenPaid` at the old value and inflating `totalSupply`/`_balances[attacker]`, and also inflates `pool.totalVoteInVlmgp` used by `castVotes()`'s target-vote computation [6](#0-5) .
2. Victim/bot's `castVotes()` executes, harvesting bribes and calling `queueNewRewards()`, which instantly credits `rewardPerTokenStored` based on `totalStaked()` that now includes the attacker's freshly added balance [7](#0-6) .
3. Attacker immediately calls `unvote()`, which calls `withdrawFor(msg.sender, currentVote, true)` — this triggers `updateRewards`, computing `earned()` off the just-updated `rewardPerToken`, then `_getReward` transfers the bribe token to the attacker before the balance is zeroed out [8](#0-7) [9](#0-8) .

No modifier, `nonReentrant` guard, cooldown, or time-lock prevents `vote()` immediately followed by `unvote()` in the same block; `onlyOperator` only checks the caller is the `WombatBribeManager` contract, which is satisfied normally.

### Impact Explanation
This is theft of unclaimed bribe yield: the attacker extracts a share of the harvested bribe reward proportional to a stake that contributed zero real (time-weighted) on-chain voting influence for that reward period, diluting and stealing yield rightfully due to other voters who held their vote positions through the actual accrual period. This matches the Immunefi impact class "theft of unclaimed yield."

### Likelihood Explanation
The attack requires only an unprivileged EOA with some vlMGP voting power (obtainable by locking MGP, a normal user action) and the ability to bundle three calls (`vote`, wait for/trigger `castVotes`, `unvote`) within one block or via flashbots-style bundling, which is realistic MEV tooling. It is repeatable every time `castVotes()` is called (which is incentivized via a caller fee, so it happens regularly), and capital requirement is proportional to desired reward share, not fixed.

### Recommendation
Introduce time-weighting or a minimum holding period before a `stakeFor` position becomes eligible for a pending bribe distribution (e.g., checkpoint reward eligibility based on vote weight duration, or require `castVotes()` to snapshot eligible stakers before applying new votes/rewards). Alternatively, disallow `unvote()` in the same block as the `stakeFor`/`castVotes` that granted the reward, or convert `BribeRewardPool` reward accounting to a duration-based streaming model (`rewardRate`/`periodFinish`) instead of an atomic `rewardPerTokenStored` jump.

### Proof of Concept
Foundry test plan:
1. Deploy `WombatBribeManager`, `WombatStaking`, `BribeRewardPool` (or mocked equivalents) with a mock `voter`/`veWom` that returns a fixed bribe amount from `vote()` regardless of submitted deltas (simulating time-accrued bribes independent of current-block deltas).
2. Set up two users: `victimVoter` who voted a fixed amount in a prior block, and `attacker` (unprivileged EOA) with vlMGP voting power.
3. In one test transaction sequence (simulating same-block execution):
   - `attacker.vote([lp], [largeDelta])`
   - `someone.castVotes(false)` — triggers `queueNewRewards` on the pool rewarder with a harvested bribe amount.
   - `attacker.unvote(lp)`
4. Assert: `attacker` received bribe tokens via `_getReward` in `unvote()`, and the amount is greater than zero and proportional to `largeDelta / (largeDelta + victimVoter's stake)`.
5. Assert `victimVoter`'s subsequent `claimBribe([lp])` yields strictly less bribe reward than it would have if the attacker's flash stake had not occurred (compare against a control run without attacker's vote/unvote).
6. Assert the attacker's `unvote()` returns `pool.totalVoteInVlmgp` to pre-attack level while the actual on-chain `voter.vote()` delta already applied in step 3 remains until the next `castVotes()`, confirming the on-chain/off-chain accounting mismatch.

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

**File:** wombat/WombatStaking.sol (L374-411)
```text
        uint256[][] memory rewardAmounts = voter.vote(_lpVote, _deltas);
        rewardTokens = new IERC20[][](rewardAmounts.length);
        callerFeeAmounts = new uint256[][](rewardAmounts.length);

        for (uint256 i; i < rewardAmounts.length; i++) {

            address bribesContract = address(voter.infos(_lpVote[i]).bribe);

            if (bribesContract != address(0)) {
                rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens();
                callerFeeAmounts[i] = new uint256[](rewardAmounts[i].length);

                for (uint256 j; j < rewardAmounts[i].length; j++) {
                    uint256 rewardAmount = rewardAmounts[i][j];
                    uint256 callerFeeAmount = 0;

                    if (rewardAmount > 0) {
                        // if reward token is bnb, wrap it first
                        if (address(rewardTokens[i][j]) == address(0)) {
                            Address.sendValue(payable(wbnb), rewardAmount);
                            rewardTokens[i][j] = IERC20(wbnb);
                        }

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

**File:** rewards/BribeRewardPool.sol (L72-100)
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

    /* ============ Internal Functions ============ */

    function _getReward(address _account) internal virtual {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            uint256 reward = earned(_account, rewardToken);
            if (reward > 0) {
                userRewards[rewardToken][_account] = 0;
                IERC20(rewardToken).safeTransfer(_account, reward);
                emit RewardPaid(_account, _account, reward, rewardToken);
            }
        }
    }    
```

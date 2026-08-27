### Title
Reward sniping via just-in-time vote() before harvestSinglePool() lets an existing vlMGP holder steal bribe yield earned by long-term voters - ([File: rewards/BaseRewardPoolV2.sol], [File: wombat/WombatBribeManager.sol])

### Summary
`BaseRewardPoolV2._provisionReward` credits newly harvested bribes to `rewardPerTokenStored` in a single lump sum based on `totalStaked()` at the exact moment the reward is queued, rather than streaming it over time (no `rewardRate`/`periodFinish` mechanism as in the standard Synthetix pattern). Because `WombatBribeManager.vote()` lets any vlMGP holder call `stakeFor` on a pool's `BribeRewardPool` with no cooldown, an attacker who already holds locked vlMGP can reallocate a large vote weight into a pool immediately before `harvestSinglePool`/`castVotes` triggers `queueNewRewards`, capture a pro-rata share of that harvest, then immediately reallocate the vote elsewhere.

### Finding Description
`vote()` calls `IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, delta)` with no time restriction: [1](#0-0) 

`stakeFor` is guarded by the `updateRewards` modifier, which checkpoints the account's reward using its balance/paid index *before* the stake is applied, then sets `userRewardPerTokenPaid` to the *current* `rewardPerTokenStored`: [2](#0-1) [3](#0-2) 

This checkpoint is correct for preventing the attacker from claiming rewards accrued *before* they staked — the exploitable issue is what happens *after*. `harvestSinglePool` triggers `wombatStaking.vote(0, ...)`, which harvests bribes and calls `queueNewRewards` → `_provisionReward`, which increases `rewardPerTokenStored` **instantly** in proportion to the reward amount divided by `totalStaked()` at that block: [4](#0-3) [5](#0-4) 

Since the attacker's stake was added to `totalSupply` a block (or same-tx) before this reward injection, their `_balances` entry participates in the pro-rata split of the newly harvested amount even though they contributed zero time-weighted exposure/voting influence toward generating that bribe. Every existing voter's `_earned` share of that specific harvested batch is diluted by the attacker's freshly added balance: [6](#0-5) 

The attacker can then call `vote()` again with a negative delta (or `unvote`) to withdraw the stake and redirect vote weight elsewhere, since `withdrawFor` has no lockup either: [7](#0-6) 

No modifier, cooldown, or minimum holding period was found in `WombatBribeManager.sol` that prevents a vote-then-immediately-unvote pattern around a harvest call.

### Impact Explanation
This is a theft of unclaimed yield: the total bribe harvested is a fixed pool-wide amount, and `rewardPerTokenStored` is bumped by `amount/totalStaked()` at the instant of harvest. By inflating `totalStaked()` immediately beforehand, the attacker captures a slice of that fixed pot that would otherwise have accrued entirely to the pool's genuine, time-weighted voters — a direct redistribution of yield away from long-term voters to a just-in-time staker. This matches the Immunefi "theft of unclaimed yield" impact class. It does not affect principal (vote power/vlMGP itself is unaffected) and does not freeze funds, only skims bribe rewards.

### Likelihood Explanation
- The attacker must already hold locked vlMGP with nonzero `getUserVotable(msg.sender)` capacity — this cannot be flash-loaned (vote-locking is a real capital/time commitment), so this is not a zero-cost or fully permissionless attack; it requires an existing MGP staker/voter.
- Given that precondition, the exploit is fully permissionless and repeatable every harvest cycle: `harvestSinglePool`/`castVotes` are public functions callable by anyone, so an attacker can watch the mempool (or simply call harvest themselves right after voting) and reallocate vote weight into whatever pool is about to be harvested, repeating this for every pool and every harvest indefinitely.
- No cooldown, minimum staking duration, or streaming-reward mechanism was found to block this, making it a low-friction, repeatable griefing/skimming strategy for any existing vlMGP holder.

### Recommendation
Introduce time-weighted reward streaming instead of instantaneous lump-sum crediting, e.g., adopt the standard Synthetix `rewardRate` + `periodFinish` pattern so that newly queued rewards vest linearly over a duration rather than being immediately attributable to whatever balance exists at the harvest block. Alternatively, enforce a minimum staking duration (time-lock) on `stakeFor`/vote reallocations before a user's added balance becomes eligible for a pending harvest, or snapshot voter shares prior to `queueNewRewards` and exclude balance added in the same block/harvest cycle.

### Proof of Concept
Foundry test plan:
1. Deploy `WombatBribeManager`, a `BribeRewardPool` for pool `LP1`, and mock `wombatStaking`/`voter` contracts. Fund a baseline voter `Alice` who votes `+100` vlMGP into `LP1` well before any harvest.
2. Simulate bribe accrual and call `harvestSinglePool([LP1])` once with only Alice staked — record `earned(Alice, rewardToken)`.
3. Reset state. Have Alice vote `+100` again, then in the same transaction batch have attacker `Bob` (who already holds sufficient vlMGP elsewhere) call `vote([LP1], [+100])` immediately before `harvestSinglePool([LP1])`, then call `vote([LP1], [-100])` immediately after in the next transaction.
4. Assert `earned(Bob, rewardToken) > 0` despite Bob having zero prior time-weighted exposure to `LP1`.
5. Assert `earned(Alice, rewardToken)` after Bob's front-run is strictly less than Alice's `earned` in the baseline (step 2) scenario, demonstrating dilution/theft of yield that should have gone entirely to Alice.

### Citations

**File:** wombat/WombatBribeManager.sol (L193-206)
```text
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

**File:** wombat/WombatBribeManager.sol (L298-311)
```text
    /// @notice Cast a zero vote to harvest the bribes of selected pools
    /// @notice this  function has a lesser importance than casting votes, hence no rewards will be given to the caller.
    function harvestSinglePool(address[] calldata _lps) public {
        uint256 length = _lps.length;
        int256[] memory votes = new int256[](length);
        address[] memory rewarders = new address[](length);
        for (uint256 i; i < length; i++) {
            address lp = _lps[i];
            Pool storage pool = poolInfos[lp];
            rewarders[i] = pool.rewarder;
            votes[i] = 0;
        }
        wombatStaking.vote(_lps, votes, rewarders, address(0));
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

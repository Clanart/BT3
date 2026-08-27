### Title
Vote-sniping of bribe rewards via same-block vote → harvestSinglePool → unvote due to non-time-weighted rewardPerToken accounting - (File: rewards/BaseRewardPoolV2.sol / BribeRewardPool.sol / wombat/WombatBribeManager.sol)

### Summary
`WombatBribeManager.vote()` and `unvote()` allow a vlMGP holder to move voting weight into a bribe pool's `BribeRewardPool` and remove it again with no minimum holding period. Because bribe accrual uses the standard `rewardPerTokenStored` balance-weighted model (`BaseRewardPoolV2._provisionReward`/`_earned`), an attacker can stake right before `harvestSinglePool`/`castVotes` triggers `queueNewRewards`, then immediately `unvote` with `claim=true` to capture a pro-rata share of the whole epoch's bribes for only one block of exposure.

### Finding Description
`vote()` calls `IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, delta)` [1](#0-0)  which, via `updateRewards`, snapshots the attacker's `userRewardPerTokenPaid` at the *current* `rewardPerTokenStored` before increasing `_balances`/`totalSupply` [2](#0-1) . This part is correct in isolation — the attacker does not retroactively claim past rewards.

The exploit is in what happens next: when anyone calls `harvestSinglePool` (zero-vote call) or `castVotes`, `WombatStaking.vote` forwards harvested bribe tokens into `IBaseRewardPool(_rewarders[i]).queueNewRewards(...)` [3](#0-2) . `_provisionReward` then spreads the *entire* freshly-harvested reward amount over `totalStaked()` **as of that instant** [4](#0-3) , i.e. `rewardPerTokenStored += amountReward * 1e_dec / totalStaked()`. This is a balance-at-harvest-time weighting, not a time-integral (seconds-staked) weighting — there is no `lastUpdateTime`/`periodFinish` streaming mechanism as in typical time-weighted staking reward pools.

Immediately after harvest, the attacker calls `unvote(lp)`, which calls `withdrawFor(msg.sender, currentVote, true)` [5](#0-4) . `withdrawFor`'s `updateRewards` modifier computes `_earned` using the attacker's full `_balances[_for]` (now including their vote delta) against the just-increased `rewardPerToken`, then `_getReward` pays it out [6](#0-5) [7](#0-6) . The attacker thus collects `delta / totalStaked_at_harvest * harvestedRewards`, identical to what a voter who had been staked for the whole epoch would earn per unit of vlMGP — despite having been staked for only 1-2 blocks.

Neither `vote()` nor `unvote()` enforces any minimum staking duration, cooldown, or epoch-lock [8](#0-7) , and `updateRewards`/`_earned` contain no time-weighting term [9](#0-8) , so nothing in the existing checks prevents this "flash-vote" pattern.

### Impact Explanation
This is a **theft of unclaimed yield** from long-term voters: bribe rewards that should accrue proportional to vlMGP-time (seconds staked × amount) are instead diluted among whoever holds a large balance at the exact harvest block, transferring value from genuine long-term voters to a last-block "sniper." The attacker needs no special privilege — only vlMGP votable balance and knowledge of when `harvestSinglePool`/`castVotes` will be called (which is a public, permissionless, and often predictable/mempool-visible transaction). Loss scales with harvested bribe size and can be repeated every harvest cycle across every active pool.

### Likelihood Explanation
Preconditions are modest: the attacker must already hold enough vlMGP to pass `getUserVotable` for the desired delta (this requires having locked MGP into vlMGP, which is a real economic cost but not privileged access), and must front-run/back-run a `harvestSinglePool`/`castVotes` transaction, which is trivial via mempool monitoring since these are permissionless calls anyone can trigger. The attack is fully repeatable across pools and harvest cycles, and requires only two extra transactions (`vote` then `unvote`) around the harvest, all executable by an ordinary EOA.

### Recommendation
Convert bribe reward accrual to a time-weighted (streamed) model, e.g., Synthetix-style `rewardRate`/`periodFinish`/`lastUpdateTime` so `rewardPerToken` integrates supply over elapsed time rather than being lump-summed against instantaneous `totalStaked()`. Alternatively, enforce a minimum vote-lock duration (e.g., require votes remain for at least one full epoch before `unvote` is allowed) so voting weight cannot be moved in and out within the same harvest window, closing the "flash-vote" window entirely.

### Proof of Concept
Foundry test outline:
1. Deploy `WombatBribeManager`, `WombatStaking`, `BribeRewardPool` for a pool `lp`, with `voter`/bribe mock contract returning a fixed bribe reward amount on `vote()` calls.
2. Honest voter `A` locks vlMGP and calls `vote([lp],[+1000e18])` at epoch start (block T0).
3. Advance many blocks to simulate a full epoch elapsing with no other stakers.
4. At block T_n-1, attacker `B` (holding vlMGP, e.g. 1000e18 votable) calls `vote([lp],[+1000e18])`.
5. At block T_n, anyone calls `harvestSinglePool([lp])`, causing the mock bribe contract to pay bribe rewards R into `BribeRewardPool` via `queueNewRewards`, doubling `totalStaked()` (A:1000, B:1000) at that instant.
6. At block T_n+1, attacker `B` calls `unvote(lp)`, which internally calls `withdrawFor(B, 1000e18, true)`, transferring `B`'s claimed bribe reward.
7. Assert: `B`'s claimed reward ≈ R/2 despite being staked for only 2 blocks, while `A` (staked the entire epoch, ~T_n blocks) can only claim the remaining R/2 when later calling `claimBribe`.
8. Assert the reward-per-vlMGP-per-block ratio for `B` is orders of magnitude higher than for `A`, demonstrating the time-weighting violation (Conservation invariant broken: reward should be proportional to `staked_amount × time_held`, not `staked_amount` alone at harvest).

### Citations

**File:** wombat/WombatBribeManager.sol (L182-237)
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

    /// @notice Unvote from an inactive pool. This makes it so that deleting a pool, or changing a rewarder doesn't block users from withdrawing
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

**File:** wombat/WombatStaking.sol (L408-411)
```text
                        rewardAmount -= protocolFee;
                        rewardAmount -= callerFeeAmount;
                        IERC20(rewardTokens[i][j]).safeApprove(_rewarders[i], rewardAmount);
                        IBaseRewardPool(_rewarders[i]).queueNewRewards(rewardAmount, address(rewardTokens[i][j]));
```

**File:** rewards/BaseRewardPoolV2.sol (L102-120)
```text
    modifier updateReward(address _account) {
        _updateFor(_account);
        _;
    }

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

**File:** rewards/BaseRewardPoolV2.sol (L301-312)
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

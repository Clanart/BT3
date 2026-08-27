### Title
Vote-weight front-running of `castVotes()`/`harvestAll()` lets an existing vlMGP holder capture disproportionate bribe yield in `DelegateVoteRewardPool` - ([File: rewards/DelegateVoteRewardPool.sol])

### Summary
`DelegateVoteRewardPool._updateVote()`/`stakeFor`/`withdrawFor` distribute newly harvested bribe rewards purely by current `_balances[_for] / totalSupply` at the moment `_queueNewRewardsWithoutTransfer` runs, with no time-weighting or minimum holding period. Any existing vlMGP holder can reallocate their already-locked vote weight into the delegate pool immediately before `WombatBribeManager.castVotes()` triggers `harvestAll()`, then reallocate it back out immediately after, capturing a share of the harvested bribes proportional to their inflated momentary balance rather than their real staking time.

### Finding Description
`stakeFor`/`withdrawFor` on `DelegateVoteRewardPool` are `onlyOperator`-gated [1](#0-0) , but the operator is `WombatBribeManager`, and its public `vote()` function calls `stakeFor`/`withdrawFor` on behalf of `msg.sender` whenever a delta is passed for a pool, including the `delegatedPool` [2](#0-1) . The only cap on how much a user can allocate is `getUserVotable(msg.sender)` = their already-locked vlMGP balance [3](#0-2) [4](#0-3) . There is no cooldown, minimum-hold duration, or per-block/per-epoch restriction preventing a user from shifting their entire existing vote allocation into `delegatedPool` and back out within the same transaction/block.

When `castVotes()` runs, it recomputes on-chain votes and then calls `IDelegateVoteRewardPool(delegatedPool).harvestAll()`, which pulls bribes and calls `_manageRewards` → `_queueNewRewardsWithoutTransfer` [5](#0-4) [6](#0-5) . That function updates `rewardPerTokenStored` using the **current** `totalSupply` at harvest time [7](#0-6) . Because `stakeFor` checkpoints the depositor's reward index only at deposit time (via `updateRewards`) [8](#0-7) , a user who deposits a large vote weight one call before `harvestAll()` and withdraws it one call after receives a full pro-rata share of that harvest event's rewards despite contributing zero real accrual time, diluting genuine long-term delegate-pool stakers.

This differs slightly from the literal precondition in the question (a "freely depositable/withdrawable ERC20 staking token") — the actual mechanism is vote-weight reallocation gated by the attacker's own already-locked vlMGP balance, not a fresh flash-loanable token. However, the exploitable root cause is identical: `_updateVote()`/`stakeFor`/`withdrawFor` provide no protection against instantaneous deposit-harvest-withdraw round trips, so any vlMGP holder can execute the described sandwich using only public functions (`vote`, `castVotes`, `unvote`/`vote` with negative delta), with no owner/operator privileges required.

### Impact Explanation
Direct theft of unclaimed bribe yield from the `delegatedPool`'s legitimate long-term stakers: an attacker with existing locked vlMGP can capture a share of every harvest event proportional to a momentarily inflated balance instead of real time-weighted stake, permanently diverting yield away from honest depositors. This matches the "theft of unclaimed yield" impact class.

### Likelihood Explanation
Requires only that the attacker already holds locked vlMGP (a normal, permissionless action any MGP holder can take) and that `delegatedPool` is set and has active `votePools` — both realistic operating conditions. The `vote()`/`castVotes()` flow has no cooldown, so the attack is repeatable every time `castVotes()`/`harvestAll()` is called, and profitability scales with the attacker's locked vlMGP relative to other delegate-pool depositors.

### Recommendation
Introduce time-weighted or checkpoint-based reward accrual for `DelegateVoteRewardPool` deposits (e.g., minimum holding period before rewards are claimable, or reward accrual based on time-integrated balance rather than instantaneous balance at harvest). Alternatively, enforce a cooldown between `stakeFor`/`vote` and `withdrawFor`/`unvote` for the delegate pool, or snapshot balances used for reward distribution prior to the block in which `harvestAll()` executes.

### Proof of Concept
Foundry test plan:
1. Deploy `WombatBribeManager`, `DelegateVoteRewardPool` with mock `IWombatBribeManager`/bribe tokens, set `delegatedPool`.
2. Set up two vlMGP holders: `victim` (locks MGP and calls `vote(delegatedPool, X)` well before harvest) and `attacker` (locks MGP earlier, but keeps vote weight allocated elsewhere).
3. Immediately before triggering `castVotes()`, have `attacker` call `vote([delegatedPool], [hugeDelta])` (reallocating existing vote weight into the delegate pool), inflating `totalSupply` in `DelegateVoteRewardPool`.
4. Call `castVotes()` (or `harvestAll()` directly) to queue new bribe rewards via `_queueNewRewardsWithoutTransfer`.
5. Immediately after, have `attacker` call `vote([delegatedPool], [-hugeDelta])` to withdraw the vote weight.
6. Assert: `attacker.earned(rewardToken)` > 0 despite holding the position for the duration of a single transaction, and that `victim`'s effective share of the harvested reward (`victim.earned / totalReward`) is lower than `victim.balance / totalSupply_before_attacker_deposit`, demonstrating dilution/theft of yield proportional to the attacker's flash allocation.

### Citations

**File:** rewards/DelegateVoteRewardPool.sol (L57-66)
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
```

**File:** rewards/DelegateVoteRewardPool.sol (L97-103)
```text
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

**File:** wombat/WombatBribeManager.sol (L102-104)
```text
    function getUserVotable(address _user) public view returns (uint256) {
        return IVLMGP(vlMGP).getUserTotalLocked(_user);
    }
```

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

**File:** wombat/WombatBribeManager.sol (L218-219)
```text
        if (userTotalVotedInVlmgp[msg.sender] > getUserVotable(msg.sender))
            revert NotEnoughVote();
```

**File:** wombat/WombatBribeManager.sol (L292-293)
```text
        // send rewards to the delegate pool
        if (delegatedPool != address(0)) IDelegateVoteRewardPool(delegatedPool).harvestAll();
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

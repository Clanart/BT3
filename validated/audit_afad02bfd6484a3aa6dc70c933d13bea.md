### Title
Reward-sniping via flash-stake into `delegatedPool` around `castVotes`-triggered (or directly callable) `harvestAll` lets an attacker capture a disproportionate share of accumulated delegate bribes - ([File: wombat/WombatBribeManager.sol], [File: rewards/DelegateVoteRewardPool.sol])

### Summary
`WombatBribeManager.castVotes` unconditionally calls `IDelegateVoteRewardPool(delegatedPool).harvestAll()` whenever `delegatedPool` is set, with no access control on the caller [1](#0-0) , and `DelegateVoteRewardPool.harvestAll()` is itself `external` with no access modifier at all [2](#0-1) . The reward distribution triggered by `harvestAll` is not time-weighted: `_queueNewRewardsWithoutTransfer` bumps `rewardPerTokenStored` using the pool's `totalSupply` measured at the instant of the call [3](#0-2) , so any vlMGP freshly staked into `delegatedPool` immediately before the harvest participates fully in the entire harvested amount (which represents bribes accumulated over the whole interval since the previous harvest) even though it was staked for only one block.

### Finding Description
1. An attacker calls `WombatBribeManager.vote()` targeting the pool entry whose `rewarder` is `delegatedPool` (added via `addPool`), which invokes `IBribeRewardPool(delegatedPool).stakeFor(attacker, X)` [4](#0-3) . `stakeFor` in `DelegateVoteRewardPool` increases `totalSupply` and `_balances[attacker]`, and its `updateRewards` modifier snapshots the attacker's reward checkpoint *before* this stake is added to the pool and before any pending harvest is queued [5](#0-4) .
2. Attacker (or anyone) calls `castVotes(false)`, which at the end unconditionally executes `IDelegateVoteRewardPool(delegatedPool).harvestAll()` regardless of who the caller is [1](#0-0) . Note this same effect is also reachable by calling `harvestAll()` directly, since it has no access-control modifier [2](#0-1) .
3. `harvestAll()` calls `claimAllBribes(address(this))`, pulling the lump sum of bribes that `delegatedPool` accrued across all underlying LP pools since its *previous* claim (i.e., since the last time anyone triggered a harvest) [6](#0-5) , then routes it to `_manageRewards` → `_queueNewRewardsWithoutTransfer`, which increments `rewardPerTokenStored` by `amountReward * scale / totalSupply` using the *current* `totalSupply` (which now includes the attacker's freshly staked `X`) [7](#0-6) .
4. Because the attacker's `userRewardPerTokenPaid` checkpoint was taken before this reward bump, `earned()` computed afterward attributes the attacker a full pro-rata share (`X / totalSupply`) of the *entire* multi-period harvest, not a time-weighted share reflecting one block of participation.
5. Attacker calls `claimAllBribes(attacker)`/`getReward(attacker)` to realize the reward, then `unvote(delegatedPool)` to withdraw the stake — no cooldown/timelock was found between `vote()`/`stakeFor` and `unvote()`/`withdrawFor` in the reviewed code [8](#0-7) .

This is a real accounting flaw: `_manageRewards`/`_queueNewRewardsWithoutTransfer` distributes lump-sum harvested rewards purely by balance share at the moment of the harvest, with no streaming/rate-based or duration-weighted mechanism, unlike a typical `rewardRate`-over-time model. Existing checks (`onlyOperator` on `stakeFor`/`withdrawFor`, `updateRewards` modifier) do not prevent this because they correctly checkpoint balances/indices for the accounting model used — the model itself lacks time-weighting.

### Impact Explanation
This allows theft of unclaimed yield from long-term delegate depositors: any user who has left vlMGP delegated to `delegatedPool` across multiple bribe epochs has their proportional accrued rewards diluted by an attacker who stakes for a single block purely to be present at harvest time. This matches the "theft of unclaimed yield" impact category. The magnitude scales with the size of the accumulated bribe pot at harvest time and the attacker's temporary stake relative to `totalSupply`.

### Likelihood Explanation
The attacker needs enough vlMGP to make a meaningful temporary stake into `delegatedPool` (capital is returned immediately after `unvote`, so this is a low-capital, repeatable, permissionless attack — no privileged role required). The trigger (`castVotes` or direct `harvestAll()`) is public and uncontrolled, and no cooldown prevents `vote()` → harvest → `unvote()` within a very short window, making the attack straightforward to time and repeat every harvest cycle.

### Recommendation
Replace the instant, balance-at-harvest-time reward bump with a time-weighted/streamed distribution model (e.g., a `rewardRate` streamed over a fixed duration as in a standard `StakingRewards` design), or checkpoint `totalSupply`/balances at a fixed epoch boundary prior to harvest so that newly staked balances only start accruing from the next epoch. Alternatively, enforce a minimum staking duration/cooldown in `DelegateVoteRewardPool` (block new stakes from participating in rewards already queued, and/or delay withdrawal eligibility) so a single-block flash-stake cannot capture a full multi-period harvest.

### Proof of Concept
Foundry test plan:
1. Deploy `WombatBribeManager`, `DelegateVoteRewardPool` (`delegatedPool`), and mock bribe rewarders; set up two LP pools with accruing bribe rewards over several blocks/days, with `delegatedPool` continuously voting into them via a long-term staker `LP1` who deposits vlMGP into `delegatedPool` and never withdraws.
2. Advance time so bribes accumulate for `LP1`'s share of `delegatedPool`.
3. Attacker calls `vote()` to stake `X` vlMGP into `delegatedPool` (one block before harvest).
4. Attacker calls `castVotes(false)` (or `harvestAll()` directly), triggering the lump-sum harvest and `rewardPerTokenStored` bump.
5. Attacker calls `claimAllBribes(attacker)` to realize rewards, then `unvote(delegatedPool)` in the same or next block.
6. Assert: attacker's captured reward > `X / (totalSupply_before_harvest_including_attacker) * accumulated_reward_since_last_harvest` is proportionally unbounded by time (i.e., equal to instantaneous balance share despite one block of participation), while `LP1`'s realized share is correspondingly reduced below what a time-weighted model would allocate. Expected: test fails on a naive equality assertion (`attacker_reward` should be ~0 for one block of true time-weighted accrual) confirming the vulnerability, and passes only after implementing a time-weighted/rate-based fix.

### Citations

**File:** wombat/WombatBribeManager.sol (L196-199)
```text
                if (delta > 0) {
                    pool.totalVoteInVlmgp += uint256(delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] += uint256(delta);
                    IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta));
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

**File:** wombat/WombatBribeManager.sol (L292-293)
```text
        // send rewards to the delegate pool
        if (delegatedPool != address(0)) IDelegateVoteRewardPool(delegatedPool).harvestAll();
```

**File:** wombat/WombatBribeManager.sol (L339-368)
```text
    function claimAllBribes(address _for)
        override public
        returns (address[] memory rewardTokens, uint256[] memory earnedRewards)
    {
        address[] memory delegatePoolRewardTokens;
        uint256[] memory delegatePoolRewardAmounts;
        if (userVotedForPoolInVlmgp[_for][delegatedPool] > 0) {
            (delegatePoolRewardTokens, delegatePoolRewardAmounts) = IDelegateVoteRewardPool(delegatedPool)
                .getReward(_for);
        }

        uint256 length = pools.length;
        rewardTokens = new address[](length + delegatePoolRewardTokens.length);
        earnedRewards = new uint256[](length + delegatePoolRewardTokens.length);

        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[pools[i]];
            address lp = pool.poolAddress;
            address bribesContract = address(voter.infos(lp).bribe);
            if (bribesContract != address(0)) {
                rewardTokens[i] = address(IWombatBribe(bribesContract).rewardTokens()[0]);
                // skip the which pool not in voting to save gas
                if (userVotedForPoolInVlmgp[_for][lp] > 0) {
                    earnedRewards[i] = IBribeRewardPool(pool.rewarder).earned(_for, rewardTokens[i]);
                    if (earnedRewards[i] > 0) {
                        IBribeRewardPool(pool.rewarder).getReward(_for, _for);
                    }
                }
            }
        }
```

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

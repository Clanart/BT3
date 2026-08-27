### Title
Just-in-time voting via `WombatBribeManager.vote()`/`unvote()` allows theft of unclaimed bribe yield - (File: `wombat/WombatBribeManager.sol`, `rewards/BaseRewardPoolV2.sol`)

### Summary
`WombatBribeManager.vote()` lets any vlMGP holder freely (re)allocate voting power across pools with no lock or cooldown, and `unvote()`/negative-delta `vote()` lets them instantly withdraw that allocation. Because the underlying `BribeRewardPool` (via `BaseRewardPoolV2._provisionReward`) credits newly-harvested bribe rewards to `rewardPerTokenStored` as an instantaneous lump sum proportional to `totalStaked()` at the moment of the call — rather than streaming rewards over time — a user can stake (vote) into a pool's `BribeRewardPool` immediately before `castVotes()` harvests bribes for that epoch, capture a full pro-rata share of the entire bribe payout, and then immediately unvote/reallocate elsewhere. This is a direct analog of the reported "quick vote and withdraw" pattern, manifesting here as theft of bribe yield from users who kept their votes committed for the full period.

### Finding Description
`vote()` updates `pool.totalVoteInVlmgp` and `userVotedForPoolInVlmgp`, and calls `IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, delta)` with no time restriction: [1](#0-0) 

`unvote()` (or a negative-delta `vote()` call) removes the stake and can be called at any time with no minimum holding period: [2](#0-1) 

`castVotes()` is the periodic function that submits the aggregated votes to the real Wombat voter and harvests/forwards bribes to each pool's rewarder: [3](#0-2) 

`stakeFor`/`withdrawFor` in `BribeRewardPool` use `updateRewards`, which relies on `BaseRewardPoolV2.rewardPerToken`/`_earned`, both driven by `rewardPerTokenStored`: [4](#0-3) 

Critically, `_provisionReward` (invoked via `queueNewRewards` when bribes are forwarded after `castVotes`) adds the entire incoming reward amount to `rewardPerTokenStored` in a single lump sum divided by the *current* `totalStaked()` — there is no time-weighted streaming/vesting of the reward: [5](#0-4) 

Because reward crediting is instantaneous and snapshot-based rather than continuously accrued, whoever is staked (i.e., has voted) at the exact moment `_provisionReward` executes captures their full pro-rata share of that epoch's bribe payout — regardless of how long they had actually voted for that pool beforehand. Combined with the complete absence of a lock/cooldown on `vote()`/`unvote()`, a user can:
1. Wait until just before `castVotes()` is expected to be called (e.g., front-run the caller/keeper, or call it themselves).
2. Call `vote()` to move all of their vlMGP voting power into the target pool's `BribeRewardPool`.
3. Trigger/observe `castVotes()`, which forwards that epoch's bribe rewards and bumps `rewardPerTokenStored` for the pool.
4. Immediately call `unvote()`/negative `vote()` to withdraw and redeploy the same voting power to another pool before its next harvest.

This lets an attacker capture bribe rewards from every pool's harvest cycle using a single unit of voting power, diluting the yield that legitimate long-term voters (who kept capital locked in the pool the whole epoch) should have received — a direct theft of unclaimed bribe yield.

### Impact Explanation
This allows an attacker to systematically extract a disproportionate share of bribe rewards intended for genuine long-term voters across every pool, without ever committing sustained voting weight to any single pool. This is theft of unclaimed yield from other users, satisfying the "theft or permanent freezing of unclaimed yield" impact bar.

### Likelihood Explanation
The attack requires no special privileges — any vlMGP holder can call `vote()`/`unvote()` freely. The only requirement is timing calls around `castVotes()` invocations (which are public and callable by anyone, including the attacker, per the "gas intensive, fee given to caller" comment), making this a realistic and repeatable strategy for a sufficiently motivated, ordinary wallet.

### Recommendation
Introduce a minimum holding/lock period after `vote()` before the same voting power can be moved via `unvote()`/re-`vote()`, or switch `BribeRewardPool`/`BaseRewardPoolV2` reward accounting from lump-sum crediting to a time-weighted streaming model (similar to `MultiRewarderPerSec`) so that instantaneous stake-and-withdraw around harvest events cannot capture a full epoch's rewards.

### Proof of Concept
1. Attacker holds vlMGP with `getUserVotable(attacker)` = X.
2. Immediately before a `castVotes()` call is expected/executed, attacker calls `WombatBribeManager.vote([poolA], [X])`, which calls `BribeRewardPool(poolA.rewarder).stakeFor(attacker, X)` [6](#0-5) .
3. `castVotes()` executes, forwarding poolA's bribe rewards which triggers `queueNewRewards` → `_provisionReward`, instantly bumping `rewardPerTokenStored` for poolA based on `totalStaked()` at that moment [7](#0-6) .
4. Attacker calls `unvote(poolA)` (or `vote([poolA],[-X])`) right after, claiming the accrued reward via `withdrawFor(..., true)` and reclaiming their voting power to repeat the process on the next pool/epoch [8](#0-7) .

### Citations

**File:** wombat/WombatBribeManager.sol (L182-206)
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
```

**File:** wombat/WombatBribeManager.sol (L222-237)
```text
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

**File:** rewards/BribeRewardPool.sol (L54-85)
```text
    /// @notice Updates information for a user in case of staking. Can only be called by the Masterchief operator
    /// @param _for Address account
    /// @param _amount Amount of newly staked tokens by the user on masterchief
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

    /// @notice Updates informaiton for a user in case of a withdraw. Can only be called by the Masterchief operator
    /// @param _for Address account
    /// @param _amount Amount of withdrawed tokens by the user on masterchief
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

### Title
Reward dilution/theft via flash vote-stake immediately before bribe reward distribution - ([File: rewards/BribeRewardPool.sol])

### Summary
`BribeRewardPool.stakeFor`/`withdrawFor` credit and debit voting-weight balances instantly and the underlying reward-per-token accounting in `BaseRewardPoolV2` applies newly donated/queued rewards as an instantaneous lump-sum split across whatever `totalStaked()` is at that exact block, with no time-weighting or vesting period. An unprivileged voter can call `WombatBribeManager.vote()` to inflate their staked weight in a targeted bribe pool immediately before a permissionless harvest (`harvestSinglePool`/`castVotes`) pushes newly accrued bribes into that pool's `BribeRewardPool`, then immediately call `unvote()` with `claim=true` to withdraw and capture a share of rewards that accrued over the whole voting epoch, diluting the honest long-term voters' share.

### Finding Description
`BribeRewardPool.stakeFor`/`withdrawFor` are only gated by `onlyOperator` (the `WombatBribeManager`), and they update `totalSupply`/`_balances` synchronously and instantly: [1](#0-0) 

The reward math in `BaseRewardPoolV2` is a classic Synthetix-style `rewardPerTokenStored` accumulator, but rewards are injected as a single lump sum at `_provisionReward`/`queueNewRewards`/`donateRewards` time, split by whatever `totalStaked()` happens to be at that instant — there is no streaming/time-weighted vesting: [2](#0-1) [3](#0-2) 

`WombatBribeManager.vote()`/`unvote()` let any voter with vlMGP weight adjust their `stakeFor`/`withdrawFor` amounts in a bribe pool at will, with no cooldown or minimum holding period: [4](#0-3) 

`harvestSinglePool()` is a fully public, permissionless function that anyone can call to trigger a bribe harvest for chosen pools with a zero net vote delta (it doesn't change the real vote cast to Wombat's on-chain voter, it just harvests already-accrued bribes): [5](#0-4) 

Exploit flow:
1. Attacker holds vlMGP votable weight (their own legitimately locked MGP — no special privilege required).
2. Attacker calls `vote(pool, +delta)` right before (or in the same transaction bundle/block as) a harvest that will push newly-accrued bribes into `pool.rewarder` — this calls `BribeRewardPool.stakeFor`, instantly inflating `totalSupply` for that pool's reward pool.
3. The harvest (`harvestSinglePool`/`castVotes`) causes `_provisionReward`/`queueNewRewards` to compute `rewardPerTokenStored += amountReward * 1e_decimals / totalStaked()`, using `totalStaked()` that now includes the attacker's freshly added weight, even though that weight did not contribute to the actual Wombat vote that generated the bribe over the epoch.
4. Attacker immediately calls `unvote(pool)` → `withdrawFor(attacker, amount, true)`, which claims `earned()` for the attacker based on this newly-updated `rewardPerTokenStored`, extracting a share of the harvested bribe proportional to the attacker's transient stake.
5. Because the total reward pie is fixed and now split among a larger `totalStaked()` (attacker's stake included) with the attacker withdrawing their share immediately, honest long-term voters who held their stake through the entire accrual period receive a smaller effective payout than they would have absent the attacker's transient insertion — the attacker's added `totalSupply` denominator dilutes everyone else's share of the same lump-sum reward, and the attacker walks away with unclaimed yield they did not economically earn.

No modifier, `nonReentrant` guard, or time-based check in `BribeRewardPool`, `BaseRewardPoolV2`, or `WombatBribeManager` prevents stake/unstake immediately surrounding a reward injection — the `updateRewards` modifier only ensures accounting correctness relative to the current `rewardPerTokenStored`, it does not prevent front-running the reward injection itself.

### Impact Explanation
This is theft/dilution of unclaimed bribe yield belonging to other voters in the same bribe pool (Immunefi impact class: "Theft of unclaimed yield"). The attacker captures reward proportional to a transient stake inserted only for the harvest block, at the expense of voters who held real weight throughout the accrual period. This is repeatable every time a bribe harvest is triggered (`harvestSinglePool`, `castVotes`), and scales with the size of the vlMGP weight the attacker can wield and the size of the pending bribe.

### Likelihood Explanation
The attacker only needs vlMGP weight (obtainable by any user locking MGP — no privileged role), and the ability to call `vote`/`unvote`/`harvestSinglePool` — all public/external functions. `harvestSinglePool` is permissionless and can be called by the attacker themselves in the same block as their `vote`/`unvote`, making this fully self-contained and repeatable across every harvest cycle without needing to front-run anyone else's transaction. This significantly raises feasibility versus depending on mempool front-running.

### Recommendation
Time-weight reward accrual (e.g., streamed emission over a duration rather than instantaneous rewardPerToken jump), or require a minimum holding/lock period between `stakeFor` and eligibility for a subsequently-donated reward batch, or snapshot eligible `totalStaked()`/balances at harvest-trigger time rather than at the moment of the lump-sum credit, so stake added after a bribe was accrued cannot dilute or claim rewards accrued before it was staked.

### Proof of Concept
Foundry fork test plan:
1. Deploy/fork the Wombat voter, `WombatStaking`, `WombatBribeManager`, and a `BribeRewardPool` for a target LP, with two honest voters A and B each having voted a fixed vlMGP weight for several epochs (accrue real bribe in Wombat's bribe contract).
2. Have attacker C acquire/lock vlMGP such that they have unused votable weight.
3. Simulate the harvest trigger point (right before a `harvestSinglePool`/`castVotes` call that will pull pending bribes into the pool's rewarder):
   - Record `earned(A)` and `earned(B)` pre-attack (call as view, or snapshot state).
   - C calls `vote([pool], [+largeDelta])`.
   - Trigger `harvestSinglePool([pool])` (or `castVotes`), which pushes the pending bribe into `BribeRewardPool` via `queueNewRewards`/`donateRewards`.
   - C immediately calls `unvote(pool)` with `claim=true`.
4. Assert: `earned(C)` after step 3 > 0 despite C only holding stake for the single harvesting block/transaction, and that `earned(A)`/`earned(B)` after the harvest (recomputed via `updateFor`/`earned`) are strictly lower than they would have been had C not inserted a transient stake (i.e., compute expected reward = `bribeAmount * weight_A / (totalStaked_without_C)` vs actual `bribeAmount * weight_A / (totalStaked_with_C)`), proving dilution/theft of A and B's yield to C's benefit.

### Citations

**File:** rewards/BribeRewardPool.sol (L57-85)
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

### Title
Just-in-time vote reallocation into `BribeRewardPool` before bribe harvest allows disproportionate, cost-free theft of bribe yield from long-term voters - (File: rewards/BaseRewardPoolV2.sol, rewards/BribeRewardPool.sol, wombat/WombatBribeManager.sol)

### Summary
`BribeRewardPool.stakeFor`/`withdrawFor` (via `BaseRewardPoolV2.updateRewards`) checkpoint a user's share at `balanceOf(_account)` *before* the body mutates `_balances`, so a stake made immediately before a `queueNewRewards` call fully participates in the very next reward bump instead of only future ones. Because `WombatBribeManager.vote()` lets a user freely reallocate existing vote weight between pools with zero net capital change, and `harvestSinglePool()`/`castVotes()` (which triggers `queueNewRewards`) are fully permissionless, an attacker can atomically move voting weight into a pool right before harvesting its bribe, capture a disproportionate share of the harvested bribe, and move the weight back out - at essentially zero cost.

### Finding Description
`BaseRewardPoolV2._provisionReward` (called from `queueNewRewards`, `rewards/BaseRewardPoolV2.sol:290-313`) performs an **instant, non-time-weighted** bump to `rewardPerTokenStored`:
```
rewardInfo.rewardPerTokenStored += (_amountReward * 10**decimals) / totalStaked();
``` [1](#0-0) 

There is no `rewardRate`/`periodFinish` streaming mechanism as in classic Synthetix pools - the whole bribe amount is credited in one shot, split purely by whoever holds `_balances` at that exact moment, with no time-weighting over the period the bribe accrued.

`BribeRewardPool.stakeFor`/`withdrawFor` are guarded by `updateRewards(_for, rewardTokens)`, which snapshots `userShare = balanceOf(_account)` **before** the function body changes `_balances`, and sets `userRewardPerTokenPaid` to the *current* `rewardPerToken` for the changed balance: [2](#0-1) [3](#0-2) 

This correctly prevents a new deposit from retroactively claiming *past* accrued rewards, but does nothing to stop the newly-increased balance from fully participating in the **very next** `queueNewRewards` bump, since that bump is computed against post-stake `totalStaked()`/`_balances` with no reference to how long the balance existed.

`WombatBribeManager.vote()` lets any voter reallocate vote weight between pools with **no net new capital required** (e.g. delta of `-X` on pool A and `+X` on pool X in the same call), immediately calling `stakeFor`/`withdrawFor` on the respective `BribeRewardPool`s: [4](#0-3) 

`harvestSinglePool()` (and `castVotes()`) - which triggers `wombatStaking.vote()` and ultimately `queueNewRewards()` on the pool's `BribeRewardPool` - are public/permissionless: [5](#0-4) [6](#0-5) 

`queueNewRewards` is restricted to `onlyManager`, but `WombatStaking` is registered as the manager, and it is invoked precisely from this permissionless `vote()`/`harvestSinglePool()`/`castVotes()` flow: [7](#0-6) 

Exploit flow (fully self-contained, no mempool race needed):
1. Attacker holds vlMGP already voted for some pool A (or any pool with slack votable weight).
2. Attacker calls `vote([A, X], [-d, +d])` to move `d` vote weight into pool X - this calls `BribeRewardPool(X).stakeFor(attacker, d)`, checkpointing attacker's `userRewardPerTokenPaid` at the pre-harvest `rewardPerToken` value.
3. Attacker (or anyone) calls `harvestSinglePool([X])`, which triggers `wombatStaking.vote()` → `queueNewRewards` on `X`'s `BribeRewardPool`, bumping `rewardPerTokenStored` for the whole pending Wombat bribe based on `totalStaked()` that now includes attacker's freshly added `d`.
4. Attacker calls `vote([X, A], [-d, +d])` to move the weight back, realizing `_earned` with the inflated share via `_earned(attacker, token, d)` computed over the full `rewardPerToken` jump.
5. Attacker calls `claimBribe([X])` to receive tokens.

This captures a share of the bribe proportional to the attacker's *instantaneous* post-front-run stake rather than a time-weighted stake over the period the bribe accrued, diluting long-term voters of pool X who held their position the entire epoch.

### Impact Explanation
This constitutes theft of unclaimed yield from legitimate long-term voters of a bribed pool, redirected to a same-block/same-tx opportunistic voter. Matches the "theft or permanent freezing of unclaimed yield" Immunefi impact class explicitly in scope. The magnitude scales with the size of the harvested bribe and the fraction of `totalStaked()` the attacker can transiently command relative to other pool-X voters.

### Likelihood Explanation
- Requires the attacker to already hold vlMGP voting weight (locked MGP) allocated somewhere in the system - not flash-loanable - but crucially **no net new capital is required**, since votes can be reallocated from any other pool with slack, making the attack essentially free beyond gas.
- `harvestSinglePool`/`castVotes` are permissionless, so the attacker does not need to win a mempool race; they can trigger the whole sequence (`vote` in → `harvestSinglePool` → `vote` out) themselves in back-to-back transactions or a single contract call.
- Repeatable every time a pool has a large pending, unharvested bribe, which is publicly visible via `previewBribes`/`pendingBribeCallerFee`.
- Effectiveness depends on the attacker's vote weight being large relative to `totalStaked()` of the targeted pool at harvest time - larger for smaller/less-voted pools.

### Recommendation
Distribute bribe rewards over a fixed streaming period (rewardRate/periodFinish pattern, as used elsewhere for continuous emissions) rather than crediting the entire `queueNewRewards` amount instantly to `rewardPerTokenStored`, so a balance added moments before a harvest cannot capture a full pro-rata share of rewards that accrued before it existed. Alternatively, enforce a minimum holding/cooldown period on `vote()`/`withdrawFor` reallocations (e.g., votes locked until the next `castVotes` epoch boundary) so vote weight cannot be shuffled in and out within the same or adjacent transaction as a harvest.

### Proof of Concept
Foundry test plan:
1. Deploy `WombatBribeManager`, `WombatStaking`, and a `BribeRewardPool` for pool X with a mocked Wombat `voter`/bribe contract that has a large pending bribe for pool X.
2. Set up two vlMGP holders: `victim` (voted early for X, holds weight `V` for a full epoch) and `attacker` (holds equal vlMGP weight `d`, currently voted for pool A).
3. Simulate epoch progress so the pending bribe for X accrues.
4. In one transaction/test block: attacker calls `vote([A, X], [-d, d])`, then calls `harvestSinglePool([X])` (or `castVotes()`), then `vote([X, A], [-d, d])`.
5. Assert `earned(attacker, bribeToken)` after step 4 is significantly greater than `d/(V+d) * bribeAmount * (attacker's actual holding time / epoch length)` (i.e., disproportionate to time-weighted fair share), while `earned(victim, bribeToken)` is correspondingly diluted below the amount they would have received had the attacker not transiently joined.
6. Compare against a control run where the attacker never votes for X, showing `victim`'s `earned()` in the control exceeds `earned()` in the attack scenario despite identical bribe input and identical `victim` holding period.

### Citations

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

**File:** rewards/BaseRewardPoolV2.sol (L273-286)
```text
    function queueNewRewards(uint256 _amountReward, address _rewardToken)
        override
        external
        onlyManager
        returns (bool)
    {
        if (!isRewardToken[_rewardToken]) {
            rewardTokens.push(_rewardToken);
            isRewardToken[_rewardToken] = true;
        }

        _provisionReward(_amountReward, _rewardToken);
        return true;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L304-312)
```text
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

**File:** wombat/WombatStaking.sol (L363-418)
```text
    function vote(
        address[] calldata _lpVote,
        int256[] calldata _deltas,
        address[] calldata _rewarders,
        address caller
    ) external returns (IERC20[][] memory rewardTokens, uint256[][] memory callerFeeAmounts) {
        if(msg.sender != bribeManager)
            revert OnlyBribeMamager();
            
        if (_lpVote.length != _rewarders.length || _lpVote.length != _deltas.length)
            revert LengthMismatch();
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
                    }

                    callerFeeAmounts[i][j] = callerFeeAmount;
                }
            }
        }
    }
```

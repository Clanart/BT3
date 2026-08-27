### Title
Unbounded growth of `rewardTokens[]` in `BribeRewardPool`/`BaseRewardPoolV2` via permissionless `castVotes` can permanently freeze bribe rewards - (File: contracts/rewards/BaseRewardPoolV2.sol, contracts/wombat/WombatStaking.sol, contracts/wombat/WombatBribeManager.sol)

### Summary
`WombatBribeManager.castVotes()`/`voteAndCast()` are callable by any ordinary wallet (with an incentive fee for the caller) and internally invoke `WombatStaking.vote()`, which for each voted pool pulls whatever reward tokens the external Wombat bribe contract currently exposes (`IWombatBribe(bribesContract).rewardTokens()`) and pushes them into the corresponding `BribeRewardPool`'s `rewardTokens[]` array via `queueNewRewards`. Because the set of bribe tokens on the external Wombat voter/bribe contracts is itself permissionless (third parties can add new bribe tokens for any pool at will), this array can grow without bound purely through normal, unprivileged protocol usage — exactly the class of bug described in the report (unbounded iteration growing over time until it exceeds the gas limit and freezes funds).

### Finding Description
`BaseRewardPoolV2.queueNewRewards` pushes any never-seen reward token onto `rewardTokens[]`: [1](#0-0) 

This function is gated by `onlyManager`, but the manager for a `BribeRewardPool` is `WombatStaking`, and `WombatStaking.vote()` calls `queueNewRewards` automatically for every reward token the external bribe contract reports, with no cap or whitelist on how many distinct tokens can be registered: [2](#0-1) 

`WombatStaking.vote()` is only gated by `onlyBribeManager` (i.e. it must be called through `WombatBribeManager`), but `WombatBribeManager.castVotes()` itself has no access restriction — any wallet can call it (and is explicitly incentivized to, per the docstring "this function will be gas intensive, hence a fee is given to the caller"): [3](#0-2) 

Once a reward token is registered, `rewardTokens[]` is iterated on every subsequent interaction with that pool — `_updateFor` (invoked by the `updateReward`/`updateRewards` modifiers on stake/unstake/claim) and `getReward`/`_multiClaim`-style flows: [4](#0-3) 

`WombatBribeManager.claimAllBribes`/`_claimBribeFor`/`unvote` all trigger these reward-pool functions for ordinary users trying to vote, unvote, or claim bribes: [5](#0-4) [6](#0-5) 

Since the token set originates from an external, permissionless bribe-registration mechanism on Wombat (any briber can add a new bribe/reward token for a pool), and `castVotes` that ingests these tokens is itself callable by any wallet, `rewardTokens[]` for a given `BribeRewardPool` can grow unboundedly through entirely ordinary, unprivileged protocol activity — with no admin action required at any point.

### Impact Explanation
As `rewardTokens[]` grows, every `updateReward`/`_updateFor`/`getReward` call for that pool becomes proportionally more expensive. Once the array grows large enough, every vote, unvote, and bribe-claim transaction touching that pool will exceed the block gas limit, permanently freezing any unclaimed bribe rewards held in that `BribeRewardPool` and preventing users from voting/unvoting for that pool ever again. This matches the reported bug class: unbounded iteration on a growing array reachable from ordinary transactions leading to permanent freezing of user funds (unclaimed yield).

### Likelihood Explanation
Likelihood is high because: (1) `castVotes`/`voteAndCast` require no privileged role and are explicitly incentivized for public callers; (2) the token registration path (`queueNewRewards`) is triggered automatically for whatever tokens the external bribe contract lists, with `isRewardToken` only deduplicating, not capping growth; (3) bribing on external DEX voter/bribe systems (like Wombat's) is typically permissionless for third parties, so over time many distinct low-value/spam bribe tokens can accumulate for popular pools without any gatekeeping by this protocol.

### Recommendation
Cap the number of reward tokens a `BribeRewardPool`/`BaseRewardPoolV2` can register (e.g. a fixed `MAX_REWARD_TOKENS`), or require owner/manager approval before a new token discovered via `WombatStaking.vote()` is admitted into `rewardTokens[]`, falling back to holding/queuing unapproved tokens without adding them to the iterated array. Alternatively, replace the O(n) iteration in `_updateFor`/`getReward` with a per-token pull-based accounting model (e.g., precomputed indices or a mapping-based claim mechanism) so pool operations no longer need to loop over the full reward-token history.

### Proof of Concept
1. Attacker (or any group of users) repeatedly adds many distinct low-value ERC20 tokens as bribes on the underlying Wombat voter/bribe contract for a specific LP pool that this protocol has registered via `WombatBribeManager.addPool`.
2. Any ordinary wallet calls `WombatBribeManager.castVotes(false)` (or `voteAndCast`). This reaches `WombatStaking.vote()`, which for that pool iterates `IWombatBribe(bribesContract).rewardTokens()` and calls `IBaseRewardPool(_rewarders[i]).queueNewRewards(...)` for each new token, appending it to `BribeRewardPool.rewardTokens[]`. [7](#0-6) 
3. Repeating step 1-2 over time (each new bribe cycle can introduce new tokens) grows `rewardTokens[]` for that pool without bound.
4. Once the array is large enough, any call into `_updateFor`/`getReward` for that pool (triggered by `claimBribeFor`, `claimAllBribes`, `unvote`) exceeds the block gas limit and reverts, permanently freezing any unclaimed bribe rewards and blocking further voting/unvoting on that pool. [4](#0-3)

### Citations

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

**File:** rewards/BaseRewardPoolV2.sol (L329-335)
```text
    function _updateFor(address _account) internal {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            // if a reward stopped queuing, no need to recalculate to save gas fee
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;
```

**File:** wombat/WombatStaking.sol (L378-412)
```text
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

**File:** wombat/WombatBribeManager.sol (L239-276)
```text
    /// @notice cast all pending votes
    /// @notice this  function will be gas intensive, hence a fee is given to the caller
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
```

**File:** wombat/WombatBribeManager.sol (L401-406)
```text
    function _claimBribeFor(address[] calldata lps, address _for) internal {
        uint256 length = lps.length;
        for (uint256 i; i < length; i++) {
            IBribeRewardPool(poolInfos[lps[i]].rewarder).getReward(_for, _for);
        }
    }    
```

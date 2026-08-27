## Title
Unbounded Growth of the Bribe Manager `pools` Array Causes Gas-DoS on Vote Casting and Bribe Harvesting - (File: `wombat/WombatBribeManager.sol`)

### Summary
`WombatBribeManager` stores every bribe-eligible LP pool in a single dynamic array `pools` that only grows as the protocol onboards new Wombat pools, and both `castVotes()` and `claimAllBribes()` unconditionally iterate over the *entire* `pools` array on every invocation. This is the same anti-pattern flagged in the external report for MetaVesT's `consentCheck` (unbounded iteration over an ever-growing set instead of using an `EnumerableSet`/paginated approach), and it is reachable by any ordinary wallet since neither function is access-restricted.

### Finding Description
`pools` is a plain `address[]` that is appended to via `addPool` and never shrinks in normal operation: [1](#0-0) 

`castVotes`, which is publicly callable by any user ("this function will be gas intensive, hence a fee is given to the caller"), loops over `pools.length` to rebuild the vote/rewarder arrays and recompute each pool's target vote before calling `wombatStaking.vote(...)`: [2](#0-1) 

Similarly, `claimAllBribes` iterates over the full `pools` array to check/claim each user's bribe rewards: [3](#0-2) 

As the protocol adds more Wombat pools over time (a normal, expected operational action, not malicious governance), the gas cost of `castVotes` and `claimAllBribes` grows linearly and unboundedly, exactly mirroring the `consentCheck` issue: eventually the loop can no longer complete within the block gas limit.

### Impact Explanation
`castVotes` is the *only* function that actually propagates a user's recorded vote deltas (`vote()` writes to `poolInfos[lp].totalVoteInVlmgp` and `userVotedForPoolInVlmgp`) into the real Wombat voter contract via `wombatStaking.vote(...)`. If the `pools` array grows large enough that `castVotes` cannot fit in a block, vote rebalancing across the whole protocol becomes permanently stuck: users' on-chain vote records diverge indefinitely from the actual votes cast to Wombat, i.e. governance voting results can no longer be corrected/updated. `claimAllBribes`, which many integrators/front-ends rely on as the default "claim everything" path, would revert unconditionally once the array is large enough, permanently freezing bribe yield claims made through that entry point.

### Likelihood Explanation
Likelihood is high over time: `addPool` is a routine, expected admin operation (onboarding new Wombat LPs), not a malicious act, and there is no cap on `pools.length`. Both affected functions (`castVotes`, `claimAllBribes`) are unprivileged, user/caller-callable, and every call re-iterates the whole array rather than a bounded/paginated subset, so gas cost is monotonically increasing with protocol growth exactly as in the original report.

### Recommendation
Replace linear iteration over the full `pools` array in `castVotes` and `claimAllBribes` with a paginated/batched design (e.g., accept a `start`/`end` range or explicit pool subset, as `harvestSinglePool`/`claimBribe` already do), or use `EnumerableSet` with capped batch processing so that no single call is forced to touch the entire, ever-growing pool set.

### Proof of Concept
1. Owner repeatedly calls `addPool` as Wombat lists new LPs (normal operational growth, no malicious intent needed) until `pools.length` is large enough that a full loop iteration (`vote()` on `IBribeRewardPool`/`voter.getUserVotes` calls per pool) exceeds the block gas limit.
2. Any user calls `castVotes(false)`; the transaction reverts due to out-of-gas since `pools.length` iterations are all mandatory in a single transaction: [4](#0-3) .
3. From this point on, no user's newly-cast `vote()` deltas can ever be propagated to the real Wombat voter, and `claimAllBribes(_for)` similarly reverts for all callers, permanently freezing the default bribe-claim path: [5](#0-4) .

### Citations

**File:** wombat/WombatBribeManager.sol (L241-269)
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

**File:** wombat/WombatBribeManager.sol (L414-434)
```text
    function addPool(
        address _lp,
        address _rewarder,
        string memory _name
    ) external onlyOwner {
        // it seems we have no way to check that the LP exists
        if(_lp == address(0))
        revert ZeroAddressError();
        Pool memory pool = Pool({
            poolAddress: _lp,
            rewarder: _rewarder,
            totalVoteInVlmgp: 0,
            name: _name,
            isActive: true
        });
        if (_lp != delegatedPool) {
            pools.push(_lp); // we don't want the delegatedPool in this array
        }
        poolInfos[_lp] = pool;
        emit AddPool(_lp, _rewarder);
    }
```

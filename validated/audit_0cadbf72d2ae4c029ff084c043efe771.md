### Title
Unbounded `pools` array in `WombatBribeManager` can permanently freeze bribe voting and unclaimed bribe rewards - (`wombat/WombatBribeManager.sol`)

### Summary
`WombatBribeManager.castVotes` and `claimAllBribes` both iterate over the entire `pools` storage array with no size cap, mirroring the `GovernorAlpha` `propose`/`queue`/`execute` bug class where unbounded arrays cause functions to run out of gas. Because `pools` only grows (via `addPool`) and is never bounded, these unprivileged, publicly-callable functions can eventually exceed the block gas limit, permanently blocking voting/bribe casting and unclaimed bribe reward distribution for every user, not just the caller.

### Finding Description
`pools` is an `address[]` that grows every time a pool is registered [1](#0-0) . There is no maximum length check anywhere in the contract.

`castVotes`, which any wallet can call (including via `voteAndCast`), loops over the full `pools` array to build vote/reward arrays and forwards them to `wombatStaking.vote`: [2](#0-1) 

`claimAllBribes`, also callable by any wallet for itself, likewise iterates the entire `pools` array to compute and distribute rewards: [3](#0-2) 

As `pools.length` grows over the life of the protocol, the gas cost of both loops (plus the external calls made per iteration — `IBribeRewardPool.getReward`, `voter.infos`, etc.) grows linearly and unboundedly. Eventually, the cumulative gas required to execute a full pass over `pools` will exceed the block gas limit, making `castVotes`, `voteAndCast`, `castVotesAndClaimBribes`, and `claimAllBribes` permanently unexecutable — exactly the class of bug described in the report, where iterating an uncapped array in a critical operational function (`queue`/`cancel`/`execute` analog here being `castVotes`/`claimAllBribes`) causes out-of-gas denial of service.

### Impact Explanation
`castVotes` is the only path by which `wombatStaking.vote` is invoked, which in turn is the only mechanism that harvests bribe rewards from Wombat's voter/bribe contracts and forwards them into each pool's `BribeRewardPool` for user distribution (see `wombat/WombatStaking.sol` `vote`, called only from `castVotes`). If `castVotes` becomes unexecutable due to gas exhaustion, bribe rewards accrued in the upstream Wombat bribe contracts can never be harvested or distributed to voters, permanently freezing unclaimed bribe yield for all vlMGP voters. Similarly, `claimAllBribes` becoming unexecutable would prevent any user from claiming already-distributed but unclaimed bribe rewards across pools in a single call (individual `claimBribe` calls with smaller `lps` subsets would remain a workaround, but `castVotes` has no such workaround since it always operates over the full `pools` set).

### Likelihood Explanation
The condition is reached purely through normal, expected protocol growth — every legitimate `addPool` call (an ordinary, non-malicious admin action to onboard new supported LPs) permanently increases the iteration cost of `castVotes`/`claimAllBribes` with no way to reduce it short of `removePool`, which itself is limited to admin action and doesn't change the fact the code has no architectural cap. As the number of integrated Wombat pools increases over time, the likelihood of the loop eventually reverting due to gas rises to certainty; there is no capping mechanism analogous to what the report recommends for `GovernorAlpha`.

### Recommendation
Introduce a hard cap on `pools.length` in `addPool`, and/or refactor `castVotes` and `claimAllBribes` to support paginated/batched processing (e.g., accept a start/end index or an explicit subset of pools) so that no single transaction must iterate the entire, ever-growing `pools` array.

### Proof of Concept
1. Owner calls `addPool` repeatedly to register N Wombat LP pools, where N is large enough that a full loop over `pools` in `castVotes` (which also triggers nested per-pool external calls in `wombatStaking.vote` and `_forwardRewards`) exceeds the network's block gas limit.
2. Any wallet calls `castVotes(swapForBnb)` or `voteAndCast(...)`; the transaction reverts with an out-of-gas error because `for (uint256 i; i < length; i++)` in [4](#0-3)  cannot complete within the gas limit.
3. From this point forward, no wallet can successfully call `castVotes`, `voteAndCast`, or `castVotesAndClaimBribes`, permanently freezing the harvesting and distribution of bribe rewards for all vlMGP voters until pools are manually removed by the owner (if even feasible), demonstrating the same unbounded-array DoS root cause identified in the external report.

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

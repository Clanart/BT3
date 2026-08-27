### Title
Removed pools remain votable via stale `isActive` flag, allowing vote()-cast vlMGP to permanently dilute `totalVlMgpInVote` outside the reach of `castVotes()` - ([File: wombat/WombatBribeManager.sol])

### Summary
`removePool()` only splices the pool out of the `pools` array but never resets `poolInfos[_lp].isActive` to `false`, and `vote()` only checks `poolInfos[_lp].isActive`, not membership in `pools`. As a result, any unprivileged voter can still call `vote()` against a pool that governance has removed, incrementing `totalVlMgpInVote` and `pool.totalVoteInVlmgp` for a pool `castVotes()` will never iterate over, corrupting the proportional `targetVote` computation used for every genuinely active pool.

### Finding Description
`removePool()` performs a swap-and-pop only on the `pools` address array and does nothing to `poolInfos[_lp]`: [1](#0-0) 

`vote()` gates entirely on `poolInfos[_lps[i]].isActive`, with no check that `_lps[i]` is still present in the `pools` array: [2](#0-1) 

Because `isActive` was never cleared by `removePool()`, a vote for the removed pool succeeds: `pool.totalVoteInVlmgp` and `totalVlMgpInVote` are both incremented, and `userTotalVotedInVlmgp[msg.sender]` is consumed against `getUserVotable(msg.sender)`: [3](#0-2) 

`castVotes()` however only iterates `pools` (whose length no longer includes the removed pool) to compute `targetVote = targetVoteInLMGP * totalVotes() / totalVlMgpInVote` for each *reachable* pool: [4](#0-3) 

Since `totalVlMgpInVote` includes the phantom pool's contribution but no pool in the loop attributes votes to it, every genuinely active pool's computed `targetVote` share is diluted below what it should be relative to `totalVotes()` (the real veWOM voting power), because the denominator is inflated by votes that are never forwarded to `wombatStaking.vote()`.

Note: this is not an irreversible "burn" of the voter's power — because `isActive` stays `true`, the same user can call `unvote(_lp)` on the removed pool at any time to reclaim their `userTotalVotedInVlmgp` capacity and reduce `totalVlMgpInVote` back down: [5](#0-4) 
The realistic, persistent impact is therefore the dilution/corruption of the proportional-cast ratio for all other active pools for as long as any user leaves votes parked in the removed pool, not a permanent loss of the voter's own capacity.

### Impact Explanation
While unresolved, the stale `isActive` flag lets an unprivileged user route vlMGP "votes" into a pool `castVotes()` can never process, inflating `totalVlMgpInVote` without a matching entry in the `pools` loop. This corrupts the `targetVote` calculation (`targetVoteInLMGP * totalVotes() / totalVlMgpInVote`) for every real active pool, reducing the veWOM vote weight forwarded to `wombatStaking.vote()` for legitimate pools relative to what governance/vlMGP holders intended — a governance/vote-outcome manipulation impact.

### Likelihood Explanation
Requires only a legitimate (non-malicious) prior admin call to `removePool()` — an ordinary lifecycle operation with no code path clearing `poolInfos[_lp].isActive`. After that, any holder of locked vlMGP (unprivileged) can trigger the dilution by simply calling `vote()` targeting the removed pool's address; no special capital or timing is needed, and it is repeatable for every pool that is ever removed.

### Recommendation
In `removePool()`, also set `poolInfos[pools[_index]].isActive = false` (and optionally zero `totalVoteInVlmgp`/reconcile `totalVlMgpInVote` for any outstanding votes) before removing it from the `pools` array, or have `vote()` additionally validate that the target pool is present in the `pools` array before accepting new votes.

### Proof of Concept
Foundry test plan:
1. Deploy `WombatBribeManager`, add two pools A and B via `addPool`.
2. Lock vlMGP for a test user, call `vote([A], [+X])` and `vote([B], [+Y])` to establish baseline `totalVlMgpInVote = X+Y`.
3. Governance calls `removePool(indexOf(A))` — assert `A` is gone from `pools` but `poolInfos[A].isActive == true`.
4. As the unprivileged user, call `vote([A], [+Z])` — assert it succeeds (no revert), `poolInfos[A].totalVoteInVlmgp` increases by `Z`, and `totalVlMgpInVote` increases by `Z`.
5. Call `castVotes(false)` — assert the loop only covers `pools` (now just B), and compute `targetVote` for B using the formula; assert B's `targetVote` is lower than it would be if `totalVlMgpInVote` excluded `Z` (i.e., `targetVoteInLMGP_B * totalVotes() / (X+Y+Z)` vs `.../(X+Y)`), demonstrating dilution.
6. Confirm reversibility: call `unvote(A)` as the user and assert `totalVlMgpInVote` drops back by `Z`, showing the effect is contingent on votes remaining unwithdrawn from the phantom pool rather than a permanent burn.

### Citations

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

**File:** wombat/WombatBribeManager.sol (L208-220)
```text
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

**File:** wombat/WombatBribeManager.sol (L246-269)
```text
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

**File:** wombat/WombatBribeManager.sol (L436-441)
```text
    function removePool(uint256 _index) external onlyOwner {
        uint256 length = pools.length;
        if(_index >= length) revert OutOfPoolIndex();
        pools[_index] = pools[length - 1];
        pools.pop();
    }
```

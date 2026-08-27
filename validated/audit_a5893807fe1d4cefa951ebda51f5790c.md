## Analysis

The reported bug class is a **stale/parallel-state desync bug**: `remove_price_pair()` removes an entry from one array (`price_pairs`) but forgets to clean up the corresponding entry in a parallel array (`last_tvl`), leaving orphaned state that corrupts subsequent calculations.

The direct analog in scope (explicitly permitted by the rules: "WombatBribeManager voting and bribes") is `WombatBribeManager.removePool()`.

### Title
Stale `poolInfos` entry after `removePool()` permanently corrupts vote-casting math, causing governance vote misallocation - (File: `wombat/WombatBribeManager.sol`)

### Summary
`removePool()` only swap-and-pops the `pools` address array, but never resets the corresponding `poolInfos[removedPool]` state (`isActive`, `totalVoteInVlmgp`) or reduces `totalVlMgpInVote` for votes already committed to that pool, exactly mirroring the `remove_price_pair()` / `last_tvl` desync described in the external report.

### Finding Description
`removePool` removes the pool address from the `pools` array only: [1](#0-0) 

It never sets `poolInfos[pool].isActive = false`, and never touches `poolInfos[pool].totalVoteInVlmgp`. Because `pool.isActive` stays `true` forever, ordinary users can still call `vote()` for the removed pool, since the only gate is `pool.isActive`: [2](#0-1) 

Each such vote increases `userTotalVotedInVlmgp[msg.sender]` and the global `totalVlMgpInVote` counter: [3](#0-2) 

However, `castVotes()` — the function that actually converts internal vlMGP votes into real Wombat votes — only iterates the `pools` array, which no longer contains the removed pool: [4](#0-3) 

The per-pool target allocation is computed as:
```
targetVote = targetVoteInLMGP * totalVotes() / totalVlMgpInVote;
```
Since `totalVlMgpInVote` permanently includes votes locked into the now-untracked "ghost" pool (because it can never be reduced back out through `castVotes`, and `unvote()` is the only path that decrements it but is not enforced), the denominator used to compute every remaining active pool's proportional vote share is permanently inflated relative to the votes actually contributing to the iterated `pools` set. This systematically under-allocates real Wombat votes to every legitimate, active pool for as long as any vlMGP remains committed to the removed pool.

### Impact Explanation
This is a persistent corruption of the vote-casting formula shared by every user of the protocol — it is not merely a display/accounting bug local to the removed pool. Every active pool's `targetVote` calculation in `castVotes()` is skewed by an inflated `totalVlMgpInVote` denominator, misallocating the protocol's actual on-chain Wombat votes across pools indefinitely (until every affected user manually unvotes from the removed pool, which most users have no reason to be aware they need to do). This satisfies the accepted "governance voting result manipulation" impact category, and can persist well beyond 24 hours.

### Likelihood Explanation
`removePool` is a normal, expected maintenance operation (delisting an LP/pool), not a malicious admin action — the resulting corruption is triggered by ordinary protocol operation and affects all subsequent, permissionless `vote()`/`castVotes()` calls by any wallet. No special privileges are needed to trigger or be affected by the bug once a pool has ever been removed while it still has active votes.

### Recommendation
When removing a pool, `removePool()` should also: set `poolInfos[pool].isActive = false`, and subtract `poolInfos[pool].totalVoteInVlmgp` from `totalVlMgpInVote` (mirroring how `unvote()` decrements it), so the vote-casting denominator no longer includes votes tied to pools that are no longer iterated in `castVotes()`.

### Proof of Concept
1. Admin adds pools A and B via `addPool`.
2. User votes with positive `_deltas` for pool A, incrementing `totalVlMgpInVote` and `poolInfos[A].totalVoteInVlmgp`.
3. Admin calls `removePool(indexOfA)` — `pools` array now only contains B, but `poolInfos[A].isActive` is still `true` and `totalVlMgpInVote` still includes the user's vote for A.
4. `castVotes()` iterates only `pools` (i.e., B), computing `targetVote = poolInfos[B].totalVoteInVlmgp * totalVotes() / totalVlMgpInVote`, where `totalVlMgpInVote` is inflated by the still-uncleared vote for A — under-allocating B's real vote share versus what it should proportionally receive.
5. This misallocation persists on every subsequent `castVotes()` call until users manually discover and call `unvote()` on the removed pool A, which is not obviously required and not enforced anywhere in the removal flow.

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

**File:** wombat/WombatBribeManager.sol (L208-216)
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
```

**File:** wombat/WombatBribeManager.sol (L245-269)
```text
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

**File:** wombat/WombatBribeManager.sol (L436-441)
```text
    function removePool(uint256 _index) external onlyOwner {
        uint256 length = pools.length;
        if(_index >= length) revert OutOfPoolIndex();
        pools[_index] = pools[length - 1];
        pools.pop();
    }
```

### Title
Users cannot unvote from a deactivated pool because `unvote()` reverts exactly when `isActive` is false, permanently freezing their vote allocation and bribe-pool stake - ([File: wombat/WombatBribeManager.sol])

### Summary
`WombatBribeManager.unvote()` is documented as the mechanism that lets a user pull their votes out of a pool that has been deactivated ("This makes it so that deleting a pool, or changing a rewarder doesn't block users from withdrawing"). However the implemented guard does the opposite: it reverts unless the pool is still active, so it is only usable for active pools - the one case where a user could instead just call `vote()` with a negative delta.

### Finding Description
`unvote()` reads the pool's `isActive` flag and reverts with `PoolNotActive()` when the flag is false: [1](#0-0) 

This is the same category of "invariant guard on the wrong side" bug as the reference report, where a threshold check was implemented in the wrong direction/location and let the system reach a state its own documentation says should never occur. Here, once the pool owner sets `poolInfos[_lp].isActive = false` (the pool-removal path that this function's own comment references), any user who currently has `userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] > 0` can no longer call `unvote(_lp)` to zero out their allocation, because the very first check reverts on an inactive pool instead of allowing it.

Compare this to `vote()`, which correctly reverts on `!pool.isActive` because you should not be able to add new votes to a dead pool: [2](#0-1) 

`unvote()` copies this same check verbatim, inverting its own purpose.

### Impact Explanation
Once a pool is deactivated:
- The user's `userVotedForPoolInVlmgp[msg.sender][pool.poolAddress]` amount can never be zeroed via `unvote()`.
- `userTotalVotedInVlmgp[msg.sender]` (and `totalVlMgpInVote`) remains permanently inflated by the stuck amount, since only `unvote()`/`vote()` update these values and `vote()` itself checks `pool.isActive` and will revert for the same dead pool.
- The user's corresponding stake inside `IBribeRewardPool(pool.rewarder)` (set via `stakeFor` in `vote()`) can likewise not be withdrawn through this path (`withdrawFor` is only called from `unvote()`/`vote()`), leaving bribe-pool stake and any unclaimed bribes tied to a dead pool inaccessible.
- Because `userTotalVotedInVlmgp` never decreases, the user's usable voting power (checked in `vote()` via `getUserVotable`) is permanently reduced by the locked amount, since `VLMGP.startUnlock()` also checks `totalLockAfterStartUnlock < userTotalVotedInVlmgp[msg.sender]` and will block unlocking the underlying vlMGP itself.

This results in a permanent, non-recoverable freeze of both the user's voting power and reward-pool stake tied to the deactivated pool, and it is reachable by any ordinary voting wallet through the pool-owner's normal pool-lifecycle action of deactivating a pool - no privileged action by the caller is required to trigger the freeze.

### Likelihood Explanation
Deactivating pools (setting `isActive = false`) is a normal, expected lifecycle operation in a bribe/voting system as pools get removed or replaced. Any user who has voted for a pool prior to its deactivation is affected, making this readily triggerable in normal operation rather than a contrived edge case.

### Recommendation
Invert the guard in `unvote()` so it operates specifically when needed for cleanup, i.e. remove the `isActive` check (or only require it to be true for the alternate re-vote path) so that users can always zero out their vote for a pool regardless of its active status:
```solidity
function unvote(address _lp) public {
    Pool storage pool = poolInfos[_lp];
    uint256 currentVote = userVotedForPoolInVlmgp[msg.sender][pool.poolAddress];
    // remove the `if(!pool.isActive) revert PoolNotActive();` check entirely,
    // or gate it the other way so inactive pools are exactly what's allowed here
    ...
}
```

### Proof of Concept
1. User calls `vote()` on `WombatBribeManager` for pool `P`, incrementing `userVotedForPoolInVlmgp[user][P]`, `userTotalVotedInVlmgp[user]`, and staking into `IBribeRewardPool(P.rewarder)`. [3](#0-2) 
2. The pool owner deactivates pool `P` (sets `poolInfos[P].isActive = false`) through the pool-removal admin flow referenced by `unvote()`'s own doc comment.
3. User calls `unvote(P)` intending to withdraw their stake and free up voting power as the docstring promises; the call reverts with `PoolNotActive()` because `pool.isActive` is now `false`. [4](#0-3) 
4. User's `userVotedForPoolInVlmgp`/`userTotalVotedInVlmgp` entries and bribe-pool stake for `P` remain stuck indefinitely; user's effective votable vlMGP is permanently reduced and their `IBribeRewardPool` stake for `P` cannot be withdrawn.

### Citations

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

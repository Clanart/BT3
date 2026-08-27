### Title
Users permanently unable to reclaim votes (and locked MGP) once a pool becomes inactive, because `unvote()` requires the pool to be active - ([File: wombat/WombatBribeManager.sol])

### Summary
`WombatBribeManager.unvote()` is documented to let users remove their vote allocation from a pool that has become inactive so that "deleting a pool, or changing a rewarder doesn't block users from withdrawing." [1](#0-0)  The implementation does the opposite of what the comment promises: it reverts with `PoolNotActive` whenever the pool is *not* active, i.e. it only works while the pool is still active, and is guaranteed to revert exactly in the scenario it was built for. [2](#0-1) 

### Finding Description
`unvote(_lp)` reads `poolInfos[_lp]` and reverts if `pool.isActive` is `false`: [2](#0-1) 

The `vote()` function, which is the only other path a normal user has to change their vote allocation (including reducing it to zero via a negative delta), applies the same `isActive` guard to every pool referenced in the call: [3](#0-2) 

This means that once a pool a user has voted for is no longer flagged `isActive`, that user has **no code path** to reduce or clear `userVotedForPoolInVlmgp[msg.sender][pool]` / `userTotalVotedInVlmgp[msg.sender]` for that pool — both `unvote()` and `vote()` require `pool.isActive == true`, which is precisely the state that no longer holds. This is the same bug class as TRST-M-2: a resource-removal/deactivation operation is not accompanied by validation that the invariant relied on by future user-facing operations (being able to reduce/clear a vote) can still be satisfied afterward.

The consequence reaches an ordinary wallet directly: `VLMGP.startUnlock()` enforces that a user's remaining locked MGP after unlocking must stay above their outstanding bribe-manager votes: [4](#0-3) 

Since `userTotalVotedInVlmgp[msg.sender]` can never be reduced once the voted pool is inactive (both clearing paths revert), the corresponding amount of the user's vlMGP-locked MGP becomes permanently unable to be unlocked — `startUnlock` will keep reverting with `NotEnoughLockedMPG` for any amount that would bring the remaining lock below the stuck vote figure.

### Impact Explanation
This results in a permanent freeze of a portion of an ordinary user's locked MGP (and correspondingly their voting-derived bribe rewards tied to that pool become unreachable/unclaimable via the normal claim/unvote flow), satisfying the "permanent freezing of funds" / "theft or permanent freezing of unclaimed yield" bar. It is triggered by routine, non-malicious pool maintenance (a pool becoming inactive/rewarder changed) rather than by any admin misbehavior, and the broken function is directly callable by the affected unprivileged user.

### Likelihood Explanation
Pools becoming inactive or having their rewarder changed is an expected, routine operational event for a bribe-voting protocol integrating with third-party (Wombat) pools, so any user who has voted for such a pool at the time it is deactivated is affected. The bug is deterministic (not probabilistic) once the precondition (`isActive == false` while user still has vote allocation) occurs.

### Recommendation
Fix the guard in `unvote()` so that it does **not** require `pool.isActive` to be true (the entire point of the function, per its own docstring, is to work when the pool is inactive), and add an explicit path in `vote()` to allow negative-delta (vote reduction) calls against inactive pools while still blocking new/positive votes to inactive pools. Additionally, verify during pool removal/deactivation that no correctness invariant needed by dependent contracts (e.g., `VLMGP.startUnlock`) can be permanently violated — analogous to the recommended `councilMembers.length >= threshold` check, add an analogous post-condition check or a guaranteed unwind path for outstanding votes before/at the time a pool is deactivated.

### Proof of Concept
1. User locks MGP in `VLMGP` and votes for pool `P` via `WombatBribeManager.vote()`, increasing `userVotedForPoolInVlmgp[user][P]` and `userTotalVotedInVlmgp[user]`.
2. Pool `P` is later deactivated / removed as part of normal protocol maintenance, setting/leaving `poolInfos[P].isActive == false` (or `poolInfos[P]` no longer reflecting an active pool).
3. User calls `unvote(P)` → reverts with `PoolNotActive` per `wombat/WombatBribeManager.sol:226-227`.
4. User calls `vote([P], [-currentVote])` to zero out the position → reverts with `PoolNotActive` per `wombat/WombatBribeManager.sol:191-192`.
5. `userTotalVotedInVlmgp[user]` can never be reduced for this stake.
6. User calls `VLMGP.startUnlock(amount)` for an amount that would bring `totalLockAfterStartUnlock` below `userTotalVotedInVlmgp[user]` → reverts with `NotEnoughLockedMPG` per `VLMGP.sol:280-282`, permanently freezing that portion of the user's MGP. [5](#0-4) [3](#0-2) [4](#0-3)

### Citations

**File:** wombat/WombatBribeManager.sol (L188-192)
```text

        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[_lps[i]];
            if (!pool.isActive)
                revert PoolNotActive();
```

**File:** wombat/WombatBribeManager.sol (L222-230)
```text
    /// @notice Unvote from an inactive pool. This makes it so that deleting a pool, or changing a rewarder doesn't block users from withdrawing
    function unvote(address _lp) public {
        Pool storage pool = poolInfos[_lp];
        uint256 currentVote = userVotedForPoolInVlmgp[msg.sender][pool.poolAddress];
        if(!pool.isActive)
            revert PoolNotActive();
        
        pool.totalVoteInVlmgp -= uint256(currentVote);
        userTotalVotedInVlmgp[msg.sender] -= uint256(currentVote);
```

**File:** VLMGP.sol (L275-283)
```text
    function startUnlock(uint256 _amountToCoolDown) external override whenNotPaused nonReentrant {
        if (_amountToCoolDown > getUserTotalLocked(msg.sender))
            revert NotEnoughLockedMPG();

        uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
        if (address(wombatBribeManager) != address(0) && 
            totalLockAfterStartUnlock < IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender))
            revert NotEnoughLockedMPG();

```

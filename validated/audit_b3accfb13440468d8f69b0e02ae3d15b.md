Based on my exploration of `WombatBribeManager.sol`, `VLMGP.sol`, and related contracts, I found a directly analogous logic-inversion bug: a function meant to perform a required state-sync/cleanup action is gated by a condition that prevents it from ever running in the exact scenario it was designed for — mirroring the `fill_staker_vpool()` bug where a state update path was skipped.

### Title
Users Cannot Unvote From Inactive Pools, Permanently Locking Their vlMGP Vote Weight and Blocking Unlock - ([File: wombat/WombatBribeManager.sol])

### Summary
`unvote()` in `WombatBribeManager.sol` is documented as the mechanism to let a user remove their vote weight from a pool that has been deactivated ("so that deleting a pool... doesn't block users from withdrawing"), but the function's guard condition does the opposite: it reverts unless the pool is still active.

### Finding Description
`unvote()` is intended to free a user's committed vote so it stops counting against `userTotalVotedInVlmgp`, specifically for pools that are no longer active: [1](#0-0) 

However the guard clause is inverted — it reverts if the pool is *not* active, the exact opposite of the documented intent: [2](#0-1) 

The companion `vote()` function has the same active-pool requirement, so a user also cannot zero out or reduce their vote on an inactive pool through that path either: [3](#0-2) 

Once an ordinary user has called `vote()` to allocate vlMGP voting weight to a pool (an unprivileged, normal user action), and that pool is later deactivated (a routine, non-malicious admin operation — pools are marked `isActive = false` when removed/replaced), the user's `userVotedForPoolInVlmgp` and `userTotalVotedInVlmgp` entries can never be reduced, because both `vote()` and `unvote()` require `pool.isActive == true`.

This directly propagates into `VLMGP.sol`'s `startUnlock()`, which enforces that a user cannot unlock more vlMGP than the amount not currently committed to voting: [4](#0-3) 

Since `userTotalVotedInVlmgp[msg.sender]` can never decrease for the stuck vote, any locked MGP corresponding to that voted amount can never be unlocked by the affected user.

### Impact Explanation
This causes a permanent freeze of a normal user's locked MGP tokens: an unprivileged user who voted for a pool that is subsequently deactivated has no code path (neither `vote()` nor `unvote()`) to release that vote allocation, so the corresponding portion of their vlMGP lock becomes permanently unable to pass the `NotEnoughLockedMPG` check in `startUnlock()`. This is a permanent freezing-of-funds condition for the affected user's principal MGP, satisfying the impact bar.

### Likelihood Explanation
Likelihood is moderate-to-high in normal protocol operation: any user who calls `vote()` for a pool is exposed the moment that pool is later deactivated by governance/admin for legitimate reasons (e.g., replacing a rewarder, removing a defunct pool) — a routine, expected lifecycle event, not an attack. No malicious actor is required; the bug is triggered purely by ordinary contract usage combined with normal pool lifecycle management.

### Recommendation
Invert the condition in `unvote()` (and adjust `vote()`'s negative-delta path) so that reducing/removing a vote on an inactive pool is always permitted regardless of `pool.isActive`, while only vote *increases* (`delta > 0`) should still require the pool to be active. This mirrors the fix pattern of ensuring the state-sync call is reachable in the exact condition it was designed to handle.

### Proof of Concept
1. Admin creates pool `P` and adds it to `WombatBribeManager` (active).
2. User locks MGP in `VLMGP`, then calls `vote([P], [+X])`, setting `userVotedForPoolInVlmgp[user][P] = X` and `userTotalVotedInVlmgp[user] += X`. [5](#0-4) 
3. Admin deactivates `P` (sets `isActive = false`) as part of normal pool management.
4. User attempts `unvote(P)` to free their vote weight — reverts with `PoolNotActive()` because `pool.isActive` is `false`. [6](#0-5) 
5. User attempts `vote([P], [-X])` — also reverts with the same check in `vote()`.
6. User calls `VLMGP.startUnlock(_amountToCoolDown)` for an amount that would leave `totalLockAfterStartUnlock < userTotalVotedInVlmgp(user)` (still includes the stuck `X`) — reverts with `NotEnoughLockedMPG`, permanently blocking unlock of that portion of the user's MGP. [7](#0-6)

### Citations

**File:** wombat/WombatBribeManager.sol (L182-192)
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
```

**File:** wombat/WombatBribeManager.sol (L196-199)
```text
                if (delta > 0) {
                    pool.totalVoteInVlmgp += uint256(delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] += uint256(delta);
                    IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta));
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

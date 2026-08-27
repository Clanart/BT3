### Title
`startUnlock` permanently freezes 100% of a user's MGP when their entire vote is on a pool later deactivated, because `unvote` requires the pool to still be active - ([File: VLMGP.sol], [File: wombat/WombatBribeManager.sol])

### Summary
`VLMGP.startUnlock` gates withdrawal eligibility on `WombatBribeManager.userTotalVotedInVlmgp(msg.sender)`, which is never decremented for votes cast on a pool that later becomes inactive. `WombatBribeManager.unvote`, whose stated purpose is exactly to let a user clear votes on a deactivated pool, incorrectly requires `pool.isActive == true` to succeed, so it reverts for the very pools it's meant to unblock. `vote()` also cannot be used to zero out that allocation because it reverts with `PoolNotActive` for any inactive pool included in the call. If a user's entire vote allocation sits on a single pool that is later deactivated, `userTotalVotedInVlmgp` can never be reduced, and `startUnlock` will permanently revert for any `_amountToCoolDown > 0`, freezing 100% of that user's locked MGP with no user-callable recovery path.

### Finding Description
- `VLMGP.startUnlock` enforces: `totalLockAfterStartUnlock < IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender)` reverts with `NotEnoughLockedMPG`. [1](#0-0) 
- `userTotalVotedInVlmgp[msg.sender]` is only decreased in `WombatBribeManager.vote()` (negative delta path) and in `unvote()`. [2](#0-1) 
- `vote()` reverts with `PoolNotActive` if *any* pool passed in `_lps` is inactive, so a user cannot include the now-inactive pool in a `vote()` call to drive its allocation to zero. [3](#0-2) 
- `unvote(_lp)` is documented as the escape hatch ("Unvote from an inactive pool. This makes it so that deleting a pool ... doesn't block users from withdrawing") but its guard is inverted — it reverts with `PoolNotActive` unless `pool.isActive` is `true`, i.e., it only works while the pool is still active, and does nothing for the exact scenario it claims to solve. [4](#0-3) 

Given this, once a pool is deactivated, the vote allocation on it (`userVotedForPoolInVlmgp[user][pool]`) is permanently stuck in `userTotalVotedInVlmgp[user]` — there is no code path an unprivileged user can call to reduce it. If a user's entire locked balance was voted into that single pool (`userTotalVotedInVlmgp[user] == getUserTotalLocked(user)`), then `startUnlock`'s check requires `totalLockAfterStartUnlock >= userTotalVotedInVlmgp(user) == getUserTotalLocked(user)`, forcing `_amountToCoolDown == 0` for every call — the entire principal is permanently frozen, not merely the phantom-voted share.

### Impact Explanation
This is a fund-freezing bug: an unprivileged user's entire locked MGP principal becomes permanently non-withdrawable once the single pool they voted 100% into is deactivated, with no user-accessible function to reduce the stale vote count. This matches "permanent freezing of user funds" and exceeds the 24-hour threshold — it is unbounded/permanent since no unprivileged call path exists to unwind the accounting.

### Likelihood Explanation
Preconditions are simple and require no special privilege from the attacker/victim: a user allocates their full vote weight to one pool via the normal `vote()` flow, and that pool is later deactivated (a plausible, routine lifecycle event for a bribe/voting pool — pools can be added/removed/changed over time, not solely a hypothetical "malicious admin" act). Once deactivated, the affected user's `startUnlock` call always reverts for any nonzero amount, deterministically and repeatably, with no capital or race-condition requirement — any user concentrating votes into one pool is exposed.

### Recommendation
Fix `unvote()`'s condition so it works specifically when the pool is inactive (the opposite of the current check), e.g. `if (pool.isActive) revert PoolStillActive();` and allow it to zero out `userVotedForPoolInVlmgp` / decrement `userTotalVotedInVlmgp` and `totalVlMgpInVote` for inactive pools without touching `IBribeRewardPool(pool.rewarder).withdrawFor` in a way that depends on pool activity, or otherwise provide an admin/permissionless sweep to purge stale votes on deactivated pools so `startUnlock` can compute an accurate votable balance.

### Proof of Concept
Foundry test plan:
1. Deploy `VLMGP`, `WombatBribeManager`, and a mock rewarder/pool; wire `VLMGP.wombatBribeManager` to the manager.
2. User locks `L` MGP via `VLMGP.lock(L)`.
3. Owner adds pool `A` (active) and user calls `vote([A], [L])`, so `userVotedForPoolInVlmgp[user][A] == L` and `userTotalVotedInVlmgp[user] == L`.
4. Owner deactivates pool `A` (`isActive = false`).
5. Assert `unvote(A)` reverts with `PoolNotActive`.
6. Assert `vote([A], [-int256(L)])` reverts with `PoolNotActive` (since A is in `_lps` and inactive).
7. Call `VLMGP.startUnlock(1)` (or any amount up to `L`) and assert it reverts with `NotEnoughLockedMPG`, proving `totalLockAfterStartUnlock < userTotalVotedInVlmgp(user)` is permanently true.
8. Confirm no other unprivileged function call sequence can reduce `userTotalVotedInVlmgp[user]` below `L`, demonstrating the funds are permanently frozen.

### Citations

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

**File:** wombat/WombatBribeManager.sol (L189-192)
```text
        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[_lps[i]];
            if (!pool.isActive)
                revert PoolNotActive();
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

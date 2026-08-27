### Title
`unvote()` reverts on deactivated pools (inverted `isActive` check), permanently blocking vlMGP unlock — ([File: wombat/WombatBribeManager.sol])

### Summary
`WombatBribeManager.unvote()` is documented to let users "Unvote from an inactive pool" so that pool deactivation never blocks withdrawals, but the implemented check is inverted: it reverts with `PoolNotActive()` when `pool.isActive == false`, i.e. exactly when the pool is inactive. Once a user has voted on a pool and that pool is later deactivated, the user can never reduce their `userVotedForPoolInVlmgp`/`userTotalVotedInVlmgp` for that pool, which blocks `VLMGP.startUnlock` due to its bound check against `userTotalVotedInVlmgp`.

### Finding Description
`vote()` only allows voting while `pool.isActive` is true [1](#0-0) . `unvote()` is meant to be the escape hatch for pools an admin later deactivates, per its own doc comment: "Unvote from an inactive pool. This makes it so that deleting a pool, or changing a rewarder doesn't block users from withdrawing" [2](#0-1) . However the guard is written as `if(!pool.isActive) revert PoolNotActive();`, which reverts precisely when the pool is inactive — the opposite of the documented intent [3](#0-2) .

Exploit/trigger flow:
1. Attacker (any unprivileged voter) calls `vote([lp],[100])` while `lp`'s pool is active, incrementing `userVotedForPoolInVlmgp[attacker][lp]` and `userTotalVotedInVlmgp[attacker]` [4](#0-3) .
2. Pool for `lp` is later deactivated (a normal, non-malicious admin lifecycle action, e.g. pool retirement/rewarder change).
3. Attacker calls `unvote(lp)` to reduce their vote and free up voting capacity — it reverts with `PoolNotActive()` because of the inverted check.
4. Because `userTotalVotedInVlmgp[attacker]` cannot be reduced for this position, `VLMGP.startUnlock` reverts too: it requires `totalLockAfterStartUnlock >= IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender)` [5](#0-4) . If the amount the attacker wants to unlock would push their remaining locked balance below their (now stuck) `userTotalVotedInVlmgp`, `startUnlock` reverts with `NotEnoughLockedMPG()`.

This is a genuine code defect (not merely an admin misconfiguration) — deactivating pools is expected normal operation, and the specific function whose entire purpose is to handle that case is unreachable in the one state it's designed for.

### Impact Explanation
This causes permanent freezing of the affected user's locked vlMGP (funds frozen for at least 24 hours, potentially indefinitely) because the only exit mechanism (`unvote`) for reducing vote-encumbrance on a deactivated pool is itself blocked, cascading into a block on `startUnlock` in `VLMGP`. This matches the "permanent freezing of funds" / "exit safety" Immunefi impact class, since a normal admin action (pool deactivation) combined with routine unprivileged user behavior (voting before deactivation) leaves no path to reduce the vote count and unlock.

### Likelihood Explanation
No special privileges are required — any voter who votes on a pool before it is deactivated is affected. Pool deactivation is a plausible/expected operational event (rewarder changes, pool retirement), not attacker-controlled, but the resulting freeze applies to any unprivileged holder who voted, making this highly likely to occur over the protocol's lifetime and trivial to reproduce.

### Recommendation
Fix the inverted condition in `unvote()`: only revert if the pool is still active and the caller is trying to unvote through some other public "vote-adjustment" path; for `unvote`, either remove the check entirely (since the function is meant to work on inactive pools) or invert it to `if (pool.isActive) revert PoolStillActive();` if unvoting should be reserved solely for inactive pools as documented. At minimum, ensure a user can always unwind their vote/rewarder stake for any pool, active or inactive, so `userTotalVotedInVlmgp` can always be decremented and `VLMGP.startUnlock` is never permanently blocked.

### Proof of Concept
Foundry test outline:
1. Deploy `VLMGP`, `WombatBribeManager`, and a mock pool/rewarder; set `wombatBribeManager` in `VLMGP`.
2. Attacker locks MGP via `VLMGP.lock(amount)`.
3. Attacker calls `WombatBribeManager.vote([lp], [amount])` while `poolInfos[lp].isActive == true`; assert `userTotalVotedInVlmgp[attacker] == amount`.
4. Admin (poolManager) deactivates the pool (`poolInfos[lp].isActive = false` via existing admin setter).
5. Attacker calls `WombatBribeManager.unvote(lp)`; assert it reverts with `PoolNotActive()`.
6. Attacker calls `VLMGP.startUnlock(amount)` (or any amount that would drop locked balance below `userTotalVotedInVlmgp[attacker]`); assert it reverts with `NotEnoughLockedMPG()`.
7. Assert there is no other unprivileged path to reduce `userTotalVotedInVlmgp[attacker]`, confirming funds/vlMGP position is stuck.

### Citations

**File:** wombat/WombatBribeManager.sol (L189-192)
```text
        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[_lps[i]];
            if (!pool.isActive)
                revert PoolNotActive();
```

**File:** wombat/WombatBribeManager.sol (L196-219)
```text
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

**File:** VLMGP.sol (L279-282)
```text
        uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
        if (address(wombatBribeManager) != address(0) && 
            totalLockAfterStartUnlock < IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender))
            revert NotEnoughLockedMPG();
```

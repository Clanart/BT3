### Title
`unvote()` reverts for inactive pools instead of allowing unvoting, permanently freezing locked MGP - ([File: contracts--020/wombat/WombatBribeManager.sol])

### Summary
`WombatBribeManager.unvote()` is documented to let users "Unvote from an inactive pool," but the guard condition is inverted: it reverts with `PoolNotActive()` when `pool.isActive` is `false`, i.e., exactly when the pool has been deactivated. Once a pool a user has voted for is deactivated, that user can never zero out `userVotedForPoolInVlmgp`/`userTotalVotedInVlmgp` for that pool via `unvote()`, and `VLMGP.startUnlock()` will revert for any unlock amount that would bring `getUserTotalLocked(user) - amount` below the stuck `userTotalVotedInVlmgp[user]`.

### Finding Description
In `unvote()`:
```
function unvote(address _lp) public {
    Pool storage pool = poolInfos[_lp];
    uint256 currentVote = userVotedForPoolInVlmgp[msg.sender][pool.poolAddress];
    if(!pool.isActive)
        revert PoolNotActive();
    ...
}
``` [1](#0-0) 

The NatSpec comment directly above states the opposite intent: "Unvote from an inactive pool. This makes it so that deleting a pool, or changing a rewarder doesn't block users from withdrawing" [2](#0-1) . The condition `if(!pool.isActive) revert PoolNotActive();` means unvoting is only possible while the pool is still active, and becomes impossible precisely once it is deactivated — the opposite of the documented and intended safety valve.

Once a user has voted for a pool with `vote()` (which requires `pool.isActive == true` at vote time) [3](#0-2) , `userVotedForPoolInVlmgp[user][lp]` and `userTotalVotedInVlmgp[user]` are incremented [4](#0-3) . If that pool is later deactivated, the user has no unprivileged way to reduce these values for that lp, since `vote()` also rejects deltas on inactive pools and `unvote()` reverts as shown above.

`VLMGP.startUnlock()` enforces:
```
uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
if (address(wombatBribeManager) != address(0) &&
    totalLockAfterStartUnlock < IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender))
    revert NotEnoughLockedMPG();
``` [5](#0-4) 

Because the stuck vote amount permanently counts against `userTotalVotedInVlmgp[msg.sender]`, any `_amountToCoolDown` that would push the remaining locked balance below that stuck amount causes `startUnlock` to revert, blocking withdrawal of the locked MGP corresponding to that vote weight.

### Impact Explanation
This is a permanent freezing of user funds (Immunefi: "Permanent freezing of funds"), not merely temporary. The affected MGP amount (equal to the vote weight cast on the now-inactive pool) can never be unlocked through `startUnlock`/`unlock` because `userTotalVotedInVlmgp` for that user can never be decremented for the deactivated pool's contribution. The trigger (pool deactivation) is a normal, expected lifecycle event for bribe pools (e.g., delisting an LP, migrating a rewarder) and is entirely outside the affected user's control — the user did nothing wrong by voting for a pool that was active at the time.

### Likelihood Explanation
No special capital or privilege is needed by the victim beyond having locked MGP and voted for a pool via the normal `vote()` flow. The only external dependency is that governance deactivates a pool at some point after users voted for it — an ordinary, non-malicious administrative action supported elsewhere in the contract (pools can be marked inactive, e.g., during migrations or delistings). This is fully repeatable for every voter of every pool that is ever deactivated, and is deterministic given the code as written.

### Recommendation
Invert the condition in `unvote()` to match its documented intent, e.g., only permit unconditional unvoting of inactive pools (and active pools should presumably use `vote()` with negative deltas), or simply remove the revert-on-active-pool restriction so `unvote()` succeeds regardless of `pool.isActive`, allowing users to always exit their vote position:
```solidity
function unvote(address _lp) public {
    Pool storage pool = poolInfos[_lp];
    uint256 currentVote = userVotedForPoolInVlmgp[msg.sender][pool.poolAddress];
    // remove/invert the isActive check to allow unvoting from inactive pools
    ...
}
```

### Proof of Concept
Foundry test plan:
1. Deploy `VLMGP`, `WombatBribeManager`, and a mock `IBribeRewardPool` rewarder; add a pool `lp` with `isActive = true` via the (governance) pool-add function.
2. As user Alice (unprivileged EOA): lock MGP in `VLMGP` (e.g., 100 MGP), then call `bribeManager.vote([lp], [100])`. Assert `userVotedForPoolInVlmgp[alice][lp] == 100` and `userTotalVotedInVlmgp[alice] == 100`.
3. As governance (legitimate, non-malicious lifecycle action), deactivate the pool: set `poolInfos[lp].isActive = false` via the existing pool-management setter.
4. As Alice, call `bribeManager.unvote(lp)`. Assert it reverts with `PoolNotActive()`.
5. As Alice, call `vlmgp.startUnlock(100)` (or any amount that would drop total locked below 100). Assert it reverts with `NotEnoughLockedMPG()`, since `userTotalVotedInVlmgp(alice) == 100` is unchanged and `totalLockAfterStartUnlock < 100`.
6. Confirm there is no other unprivileged code path in `WombatBribeManager` or `VLMGP` that lets Alice reduce `userVotedForPoolInVlmgp[alice][lp]` or `userTotalVotedInVlmgp[alice]` once the pool is inactive, proving the MGP is permanently locked.

### Citations

**File:** wombat/WombatBribeManager.sol (L189-192)
```text
        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[_lps[i]];
            if (!pool.isActive)
                revert PoolNotActive();
```

**File:** wombat/WombatBribeManager.sol (L196-211)
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

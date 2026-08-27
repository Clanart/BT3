### Title
`unvote()` reverts on inactive pools, blocking user withdrawal exit path exactly when it is needed - (File: wombat/WombatBribeManager.sol)

### Summary
`unvote(address _lp)` is documented as the mechanism to let users release their vote/stake position when a pool is deactivated or its rewarder is changed, but its implementation reverts with `PoolNotActive()` whenever `pool.isActive` is `false` [1](#0-0) . Since normal `vote()`/withdraw paths also require `pool.isActive == true` [2](#0-1) , once a pool is set inactive there is no function left in this contract that lets a user reduce `userVotedForPoolInVlmgp[msg.sender][pool.poolAddress]` back to zero and reclaim the corresponding `BribeRewardPool` stake/rewards.

### Finding Description
`vote()` requires `pool.isActive` to be true before allowing any positive or negative delta on that pool [3](#0-2) , so once a pool is deactivated, users can no longer call `vote()` with a negative delta to unwind their position. The dedicated escape hatch is `unvote()`, whose docstring explicitly states its purpose: "Unvote from an inactive pool. This makes it so that deleting a pool, or changing a rewarder doesn't block users from withdrawing" [4](#0-3) . However the guard condition is inverted — it reverts with `PoolNotActive()` precisely `if (!pool.isActive)` [5](#0-4) , i.e., it only works while the pool is still active (duplicating what `vote()` already does), and fails for exactly the case it was written to handle.

Once a pool's `isActive` flag is flipped to `false` (via governance/pool management, which is out of scope for the attacker but is the realistic real-world trigger event), any user with `userVotedForPoolInVlmgp[user][pool] > 0` is permanently unable to call either `vote()` (blocked by the same `isActive` check) or `unvote()` (blocked by the inverted check) for that pool. Their stake in `IBribeRewardPool(pool.rewarder)` remains locked, and their share of `userTotalVotedInVlmgp` / `totalVlMgpInVote` remains counted in the global voting accounting used by `castVotes()` to compute `targetVote = targetVoteInLMGP * totalVotes() / totalVlMgpInVote` [6](#0-5) . Because the stale pool's `totalVoteInVlmgp` can never be reduced, and the pool itself may be dropped from `pools[]` or have `castVotes()` skip it, `totalVlMgpInVote` stays inflated relative to the real amount of vlMGP-derived votes being actively distributed across live pools, permanently misallocating `targetVote` for the remaining active pools computed from `totalVotes()` (`veWom.balanceOf(wombatStaking)`).

### Impact Explanation
Users who voted for a pool prior to its deactivation have their vlMGP voting weight and their corresponding stake/reward entitlement in `BribeRewardPool` permanently frozen — there is no function path to zero out `userVotedForPoolInVlmgp` and call `withdrawFor` for that pool once `isActive` is `false`. This directly matches "Permanent freezing of funds" (locked bribe rewards / vote weight) and permanently skews the `castVotes()` vote-redistribution invariant across the remaining active pools, since `totalVlMgpInVote` never shrinks to reflect the abandoned pool's now-unwithdrawable votes.

### Likelihood Explanation
The bug is deterministic and always triggers as soon as any pool that has active voters is later deactivated by pool management (a normal operational event described directly by the code's own comment as something that must not block withdrawal). No attacker capital or special conditions are needed beyond having previously voted for a pool that later becomes inactive; every affected user hits the revert on `unvote()`. This is a straightforward logic inversion, 100% reproducible, and requires no adversarial interaction — it is a self-inflicted denial-of-withdrawal bug rather than something requiring an attacker to actively exploit against a victim.

### Recommendation
Fix the condition in `unvote()` so it does not require `pool.isActive`. Either remove the `isActive` check entirely (since `unvote()` should work regardless of pool state, as the comment states), or invert intent correctly, e.g. only guard against operating on a completely undefined pool (`pool.poolAddress == address(0)`), not on `isActive` being false. Ensure `castVotes()`'s `totalVlMgpInVote` accounting is consistent once stale pools are unvoted so `targetVote` computations remain correct for remaining active pools.

### Proof of Concept
Hardhat test plan:
1. Deploy `WombatBribeManager`, `BribeRewardPool`, `WombatStaking`, mock `veWom`/`voter` per existing test mocks.
2. Add a pool `P` via the pool-management function, set `isActive = true`.
3. User `U` locks vlMGP and calls `vote([P], [amount])`, verify `userVotedForPoolInVlmgp[U][P] == amount` and `IBribeRewardPool(P.rewarder).balanceOf(U) == amount`.
4. Pool manager deactivates `P` (`isActive = false`).
5. Call `unvote(P)` from `U` and assert it reverts with `PoolNotActive()`.
6. Call `vote([P], [-amount])` from `U` and assert it also reverts with `PoolNotActive()`, proving there is no remaining path to release the position.
7. Assert `userVotedForPoolInVlmgp[U][P]` remains `amount` indefinitely and `U`'s stake in `BribeRewardPool` remains locked, demonstrating permanent freezing.

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

**File:** wombat/WombatBribeManager.sol (L256-268)
```text
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
```

### Title
`unvote` cannot be called on a deactivated pool, permanently freezing a user's voting power and pending bribe rewards - ([File: wombat/WombatBribeManager.sol])

### Summary
`WombatBribeManager.unvote()` is meant to let a user withdraw their vote/stake from a pool even after that pool has been deactivated by the admin — the code comment explicitly states this intent ("Unvote from an inactive pool. This makes it so that deleting a pool, or changing a rewarder doesn't block users from withdrawing"). However, the function's guard does the opposite of what the comment promises: it reverts with `PoolNotActive` whenever `pool.isActive` is `false`, which is exactly the scenario the function is supposed to handle.

### Finding Description
`unvote()` is a user-callable, unprivileged function used to remove a user's vote from a Wombat pool and withdraw their `vlMGP` voting stake from the pool's `BribeRewardPool`: [1](#0-0) 

The function checks `if(!pool.isActive) revert PoolNotActive();` before doing anything else. This is the same class of bug as the miniwasm `setBeforeSendHook` issue: the code's documented/intended "reset/disable" path (unvoting from an inactive pool) is unreachable because the validation guard rejects exactly the state (`isActive == false`) that the reset path is designed for.

Once the pool is deactivated (an admin action, but the resulting state affects all users who previously voted for it and is outside their control), every affected user's call to `unvote(_lp)` for that pool will always revert. Since `vote()` only allows voting on active pools (`if (!pool.isActive) revert PoolNotActive();` in the same function, see `vote()` at lines 182-220), there is no other unprivileged path to zero out `userVotedForPoolInVlmgp[msg.sender][pool.poolAddress]` for a deactivated pool. [2](#0-1) 

### Impact Explanation
Because `userVotedForPoolInVlmgp` and `userTotalVotedInVlmgp` can never be decremented for a deactivated pool, the user's `vlMGP` voting stake tied to that pool is permanently stuck:
- `userTotalVotedInVlmgp[msg.sender]` remains inflated forever, permanently reducing the user's usable voting capacity (`getUserVotable`) for all other pools — since `vote()` requires `userTotalVotedInVlmgp[msg.sender] <= getUserVotable(msg.sender)`.
- The user's stake recorded in `IBribeRewardPool(pool.rewarder).withdrawFor(...)` for that deactivated pool can never be withdrawn via the intended path, permanently freezing the accrued/future bribe entitlement tied to that stake, since `withdrawFor` is only reachable through `unvote`/`vote` in this contract.

This qualifies as permanent freezing of funds (locked voting power and unclaimed/future bribe entitlements) and voting-result manipulation risk (since other users retain the ability to vote/unvote freely while affected users are locked out), satisfying the required impact bar.

### Likelihood Explanation
Pool deactivation is a normal, expected admin operation (removing or replacing a pool/rewarder) rather than a malicious action, and the code comment itself anticipates users needing to unvote afterward. Any user who voted for a pool before it is deactivated is unconditionally affected the next time they attempt to reclaim their vote — no attacker interaction or special conditions are needed, only the routine lifecycle event of a pool being deactivated.

### Recommendation
Remove or invert the `isActive` check in `unvote()` so that unvoting is always permitted regardless of the pool's active status, consistent with the function's own documented intent, mirroring the miniwasm mitigation of removing the blocking validation in the "disable" code path.

### Proof of Concept
1. User calls `vote()` on pool `P` with a positive delta, which succeeds because `P.isActive == true`; `userVotedForPoolInVlmgp[user][P]` and `userTotalVotedInVlmgp[user]` are incremented, and `IBribeRewardPool(P.rewarder).stakeFor(user, delta)` records the stake.
2. Admin deactivates pool `P` (sets `poolInfos[P].isActive = false`) as part of normal pool management.
3. User calls `unvote(P)` to reclaim their vote — the call reverts with `PoolNotActive` at [3](#0-2) , even though the function's own comment states this exact case should be supported.
4. The user's `vlMGP` voting capacity remains permanently locked against pool `P`, and their stake in `P`'s `BribeRewardPool` can never be withdrawn through this contract.

### Citations

**File:** wombat/WombatBribeManager.sol (L180-220)
```text
    /// @notice Vote on pools. Need to compute the delta prior to casting this.
    /// @param _deltas delta amount in vlMGP
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

Confirmed. The `unvote` function at [1](#0-0)  reverts with `PoolNotActive()` when `pool.isActive` is `false`, which is the exact opposite of what the NatSpec comment states.

### Title
`unvote()` reverts with `PoolNotActive` when pool is inactive, contradicting NatSpec and permanently stranding user vote allocation - (File: wombat/WombatBribeManager.sol)

### Summary
The `unvote(address _lp)` function is documented as "Unvote from an inactive pool" but its implementation checks `if(!pool.isActive) revert PoolNotActive();`, which reverts precisely in the inactive-pool case instead of allowing withdrawal. Once a pool is deactivated (owner-controlled, but the trigger condition here is the pool state, not an owner action performed by the attacker), any unprivileged user who previously voted for that pool cannot call `unvote` to reclaim their `vlMGP` voting weight.

### Finding Description
`vote()` at [2](#0-1)  requires `pool.isActive` to be true to add votes, and correspondingly increments `userVotedForPoolInVlmgp[msg.sender][pool.poolAddress]`, `userTotalVotedInVlmgp[msg.sender]`, and `pool.totalVoteInVlmgp`.

`unvote()` is intended, per its NatSpec at [3](#0-2) , to let a user pull their vote out specifically when a pool has become inactive ("This makes it so that deleting a pool, or changing a rewarder doesn't block users from withdrawing"). However the actual guard is inverted: [1](#0-0) 
```
function unvote(address _lp) public {
    Pool storage pool = poolInfos[_lp];
    uint256 currentVote = userVotedForPoolInVlmgp[msg.sender][pool.poolAddress];
    if(!pool.isActive)
        revert PoolNotActive();
    ...
    IBribeRewardPool(pool.rewarder).withdrawFor(msg.sender, uint256(currentVote), true);
}
```
This means `unvote` only succeeds while `pool.isActive == true` (redundant with `vote`, since votes could just be zeroed via `vote()` while active), and reverts exactly when the pool becomes inactive — the one case the function was written to handle. Once the pool is deactivated, the user's recorded vote in `userVotedForPoolInVlmgp` and `userTotalVotedInVlmgp` can no longer be reduced via `unvote`, and since `vote()` also gates on `pool.isActive` for all pools it touches (line 191-192), the user cannot zero out their allocation for the now-inactive pool via `vote()` either. This locks the corresponding portion of `userTotalVotedInVlmgp[msg.sender]`, which counts against `getUserVotable(msg.sender)` in the `vote()` function's `NotEnoughVote` check [4](#0-3) , reducing the user's ability to reallocate voting weight elsewhere.

### Impact Explanation
This freezes a portion of the user's `vlMGP` voting weight against the inactive pool indefinitely (not the underlying locked MGP or bribe tokens themselves, but the voting-weight accounting `userTotalVotedInVlmgp`), reducing their effective votable balance for future `vote()` calls until the pool is somehow reactivated or an owner/admin path resolves it. This matches a "permanent freezing of voting weight/governance-related state" impact, though it does not directly freeze principal tokens or yield — the bribe reward pool's `withdrawFor` is never reached, and no other pathway in the contract to zero this specific pool's vote appears when the pool is inactive.

### Likelihood Explanation
The precondition requires an owner action (pool deactivation) to occur after the attacker's vote, which is outside attacker control but is a normal, foreseeable protocol operation (pools do get deprecated/deactivated over the product's lifetime). No special capital or privileges are needed by the attacker beyond having voted with vlMGP once; the bug manifests for any voter, not just a contrived attacker, whenever the owner deactivates a pool that has active voters.

### Recommendation
Fix the condition to require the pool be inactive (or remove the check, if `unvote` is meant to work regardless of pool state):
```solidity
function unvote(address _lp) public {
    Pool storage pool = poolInfos[_lp];
    uint256 currentVote = userVotedForPoolInVlmgp[msg.sender][pool.poolAddress];
    if (pool.isActive)
        revert PoolStillActive(); // or simply remove the isActive check entirely
    ...
}
```

### Proof of Concept
Foundry test plan:
1. Deploy `WombatBribeManager`, register a pool `P` with `isActive = true`, and set up a mock `vlMGP` giving the attacker EOA a nonzero `getUserTotalLocked`.
2. As attacker, call `vote([P], [+X])`; assert `userVotedForPoolInVlmgp[attacker][P] == X` and `userTotalVotedInVlmgp[attacker] == X`.
3. As owner, deactivate `P` (set `poolInfos[P].isActive = false`) via whatever admin setter exists (e.g., `removePool`/`updatePool`/`setPoolActive`).
4. As attacker, call `unvote(P)`.
5. Assert the call reverts with `PoolNotActive()`.
6. Assert `userTotalVotedInVlmgp[attacker]` remains `X` and cannot be reduced by any other public function while `P.isActive == false`, demonstrating the weight is stuck.

### Citations

**File:** wombat/WombatBribeManager.sol (L189-192)
```text
        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[_lps[i]];
            if (!pool.isActive)
                revert PoolNotActive();
```

**File:** wombat/WombatBribeManager.sol (L218-219)
```text
        if (userTotalVotedInVlmgp[msg.sender] > getUserVotable(msg.sender))
            revert NotEnoughVote();
```

**File:** wombat/WombatBribeManager.sol (L222-222)
```text
    /// @notice Unvote from an inactive pool. This makes it so that deleting a pool, or changing a rewarder doesn't block users from withdrawing
```

**File:** wombat/WombatBribeManager.sol (L223-237)
```text
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

### Title
Stale `userVotedForPoolInVlmgp` entries corrupt `pool.totalVoteInVlmgp` after `removePool`+`addPool` re-adds the same LP, causing permanent `unvote()` reverts or silent deflation of vote totals - (File: wombat/WombatBribeManager.sol)

### Summary
`removePool()` only removes the LP address from the `pools` array via swap-and-pop; it never touches `poolInfos[_lp]` (does not clear `isActive`, `totalVoteInVlmgp`, or per-user `userVotedForPoolInVlmgp` entries). When the owner subsequently calls `addPool()` on the same `_lp`, `poolInfos[_lp]` is overwritten with a fresh `Pool` struct (`totalVoteInVlmgp: 0`), but each voter's stale `userVotedForPoolInVlmgp[user][lp]` balance from the prior incarnation is left untouched, creating a permanent desync that `unvote()` can trigger.

### Finding Description
`removePool` (lines 436-441) mutates only the `pools` array: [1](#0-0) 
It does not set `poolInfos[_lp].isActive = false`, so `poolInfos[_lp]` (including `totalVoteInVlmgp` and `isActive`) is left completely intact in storage after "removal."

When the owner later calls `addPool` with the same `_lp` address, `poolInfos[_lp]` is fully overwritten with a new `Pool` struct whose `totalVoteInVlmgp` is reset to `0`: [2](#0-1) 
Critically, `addPool` never clears `userVotedForPoolInVlmgp[user][_lp]` for any user, since that mapping is per-user and per-lp and is not iterated or reset anywhere in the contract.

Exploit flow:
1. Attacker calls `vote()` on `lp`, setting `userVotedForPoolInVlmgp[attacker][lp] = X` and `poolInfos[lp].totalVoteInVlmgp += X` (lines 189-206).
2. Owner calls `removePool(index)` for `lp` (pool churn / rewarder replacement) - `poolInfos[lp]` (including the `X` total and `isActive=true`) is untouched.
3. Owner calls `addPool(lp, newRewarder, name)` - `poolInfos[lp].totalVoteInVlmgp` is reset to `0`, `isActive` remains `true`. The attacker's `userVotedForPoolInVlmgp[attacker][lp] = X` is now stale and inconsistent with the fresh pool state.
4. Attacker calls `unvote(lp)` (lines 223-237). Since `pool.isActive` is `true`, the `PoolNotActive` check passes. `pool.totalVoteInVlmgp -= X` is executed against a pool whose real total from the new incarnation may be `0` or some smaller value `Y < X` contributed by other users, causing an arithmetic underflow revert (Solidity 0.8 checked math), or if `Y >= X`, silently deflating `pool.totalVoteInVlmgp` by an amount `X` the attacker never contributed to this new pool incarnation: [3](#0-2) 

Neither `vote()` nor `unvote()` nor `addPool()`/`removePool()` reconcile `userVotedForPoolInVlmgp` against pool re-incarnations, so this stale-state corruption is unavoidable once the same `_lp` is removed and re-added while any user held a nonzero vote balance.

### Impact Explanation
This corrupts `pool.totalVoteInVlmgp`, which directly feeds the proportional vote-casting formula in `castVotes()` (`targetVote = targetVoteInLMGP * totalVotes() / totalVlMgpInVote`), meaning the real votes forwarded to the Wombat `voter` contract for every pool become miscalculated - a governance voting-result manipulation / vote-integrity break. Additionally, if the underflow path triggers, `unvote(lp)` permanently reverts for the affected user on that pool, freezing their ability to reclaim/reallocate that portion of their locked-MGP voting power (their `userTotalVotedInVlmgp` also remains inflated since the decrement in the same reverted transaction never lands), a form of permanent fund/voting-power lock exceeding 24 hours.

### Likelihood Explanation
Requires an owner action (`removePool` followed later by `addPool` on the same LP address) - a plausible, non-malicious operational scenario (e.g., swapping a rewarder contract or fixing a misconfigured pool by remove+re-add). The attacker only needs to have voted on the pool prior to removal and to call the public `unvote()` function afterward - no special capital or privileges are needed beyond having previously voted, making this fully attacker-triggerable and repeatable for every pool the owner re-adds.

### Recommendation
- In `removePool`, explicitly zero out/deactivate `poolInfos[_lp]` (set `isActive = false`, and ideally iterate/clear or version pools so that stale `userVotedForPoolInVlmgp` cannot apply to a future incarnation).
- In `addPool`, refuse to re-add an `_lp` that still has an existing `poolInfos[_lp]` entry with nonzero `totalVoteInVlmgp` or unresolved user votes, or require a bulk unvote/reset step before re-add, or use a monotonically increasing pool "epoch"/id so stale `userVotedForPoolInVlmgp` entries are scoped to the old epoch and cannot be applied against a new one.

### Proof of Concept
Foundry test outline:
1. Deploy `WombatBribeManager`, register `lpA` via `addPool`, fund/lock MGP for `attacker` so `getUserVotable(attacker) >= X`.
2. `vm.prank(attacker); vote([lpA], [int256(X)])` - assert `poolInfos[lpA].totalVoteInVlmgp == X` and `userVotedForPoolInVlmgp[attacker][lpA] == X`.
3. `vm.prank(owner); removePool(indexOf(lpA))` - assert `poolInfos[lpA].isActive == true` still (bug confirmed) and `totalVoteInVlmgp == X` still.
4. `vm.prank(owner); addPool(lpA, newRewarder, "lpA")` - assert `poolInfos[lpA].totalVoteInVlmgp == 0`.
5. `vm.prank(attacker); unvote(lpA)` - expect revert due to underflow (`pool.totalVoteInVlmgp -= X` where current value is `0`), demonstrating permanent inability to reclaim voting power; alternatively have a second, honest user vote `Y >= X` on the re-added pool first, then show `unvote` by attacker succeeds and silently reduces `poolInfos[lpA].totalVoteInVlmgp` to `Y - X`, which is not backed by any real vote reduction contributed to this pool incarnation.

### Citations

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

**File:** wombat/WombatBribeManager.sol (L414-434)
```text
    function addPool(
        address _lp,
        address _rewarder,
        string memory _name
    ) external onlyOwner {
        // it seems we have no way to check that the LP exists
        if(_lp == address(0))
        revert ZeroAddressError();
        Pool memory pool = Pool({
            poolAddress: _lp,
            rewarder: _rewarder,
            totalVoteInVlmgp: 0,
            name: _name,
            isActive: true
        });
        if (_lp != delegatedPool) {
            pools.push(_lp); // we don't want the delegatedPool in this array
        }
        poolInfos[_lp] = pool;
        emit AddPool(_lp, _rewarder);
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

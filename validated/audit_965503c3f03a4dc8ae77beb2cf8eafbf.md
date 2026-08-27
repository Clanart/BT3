Confirmed: `withdrawFor` only requires `onlyOperator` (the bribe manager), so it succeeds regardless of removal from `pools` — it is not truly "orphaned" and reverts for no legitimate reason.

### Title
Vote accounting desync after `removePool(uint256)` causes miscounted global vote totals and diluted votes for active pools - ([File: wombat/WombatBribeManager.sol])

### Summary
`removePool(uint256 _index)` in `wombat/WombatBribeManager.sol` only swaps/pops the `_index` entry out of the `pools` array; it never clears `poolInfos[_lp].isActive`, `poolInfos[_lp].totalVoteInVlmgp`, or subtracts the removed pool's votes from `totalVlMgpInVote`. Because `isActive` is never flipped to `false` anywhere in this contract, the stale pool's votes remain permanently baked into `totalVlMgpInVote` while being excluded from the `pools`-array summation that `castVotes()` iterates over, permanently skewing the `targetVote` computation for every remaining active pool.

### Finding Description
`removePool` only mutates the `pools` array: [1](#0-0) . It never touches `poolInfos[_lp]` or `totalVlMgpInVote`. Nothing else in the contract ever sets `Pool.isActive` back to `false` after `addPool` sets it `true` [2](#0-1) , so `unvote()`'s guard `if(!pool.isActive) revert PoolNotActive();` never blocks a user from unvoting a removed pool [3](#0-2) , and `IBribeRewardPool(pool.rewarder).withdrawFor()` still succeeds because `BribeRewardPool.withdrawFor` only checks `onlyOperator` (the bribe manager itself), not whether the pool is still tracked in the `pools` array [4](#0-3) .

The real accounting break, however, occurs immediately upon `removePool` (independent of whether `unvote` is ever called): `totalVlMgpInVote` is only ever mutated inside `vote()`/`unvote()` [5](#0-4) , but `castVotes()` sums `pool.totalVoteInVlmgp` only over the live `pools` array to derive `targetVote = targetVoteInLMGP * totalVotes() / totalVlMgpInVote` [6](#0-5) . Once a pool is removed, its `totalVoteInVlmgp` stays counted in the `totalVlMgpInVote` denominator but drops out of the `pools`-array numerator sum used by `castVotes()`, so `sum(poolInfos[p].totalVoteInVlmgp for p in pools) != totalVlMgpInVote` from that point forward, until (if ever) the affected voters manually call `unvote()`. As long as this gap persists, every remaining active pool's `targetVote` is proportionally under-scaled relative to the real votable Wombat balance (`totalVotes()`), so less of the DAO's real veWOM voting power is actually cast than users intended.

### Impact Explanation
This is a governance/vote-integrity bug: the fraction of `totalVotes()` (real Wombat voting power) actually cast to legitimately active pools is permanently diluted for as long as the stale, removed pool's vote balance remains uncleared, which can be indefinite since nothing forces affected users to call `unvote()`. This matches the "governance voting result manipulation" impact class, indirectly reducing the bribe/reward inflow that active pools and their stakers would otherwise receive. It is not a direct fund-theft path for the attacker themselves, and no single unprivileged actor can extract value from it beyond correctly withdrawing their own already-cast vote via `unvote()`.

### Likelihood Explanation
The trigger condition (`removePool` being called on a pool that has outstanding votes) is a normal, expected lifecycle operation for the protocol owner, not a malicious or misconfigured action — pools legitimately get sunset over time. Once triggered, the desync is automatic and requires no attacker capital or special access; it persists passively until affected voters unvote. This makes it a design/cleanup gap in `removePool` rather than an attacker-crafted exploit; the `unvote()` call itself behaves correctly and is not the root cause.

### Recommendation
In `removePool(uint256 _index)`, before removing `_lp` from the `pools` array, subtract `poolInfos[_lp].totalVoteInVlmgp` from `totalVlMgpInVote` (and set `poolInfos[_lp].isActive = false`), or otherwise force-unwind/zero out the pool's outstanding vote so the global accounting stays reconciled the instant the pool is removed, rather than relying on voluntary `unvote()` calls by affected users.

### Proof of Concept
Foundry test in a fork of `WombatBribeManager`:
1. `addPool(lpA, rewarderA, "A")`, `addPool(lpB, rewarderB, "B")`.
2. User1 calls `vote([lpA], [1000e18])`; assert `poolInfos[lpA].totalVoteInVlmgp == totalVlMgpInVote == 1000e18`.
3. Owner calls `removePool(indexOf(lpA))`; assert `lpA` no longer in `pools` but `poolInfos[lpA].totalVoteInVlmgp` still `1000e18` and `totalVlMgpInVote` still `1000e18`.
4. User2 calls `vote([lpB], [1000e18])`; now `totalVlMgpInVote == 2000e18` but `sum(poolInfos[p].totalVoteInVlmgp for p in pools) == 1000e18` (only lpB, since lpA removed).
5. Call `castVotes()`; assert the computed `targetVote` for `lpB` (`1000e18 * totalVotes() / 2000e18`) is half of what it should be (`1000e18 * totalVotes() / 1000e18`), demonstrating the vote-integrity invariant `sum(poolInfos[activePool].totalVoteInVlmgp) == totalVlMgpInVote` is broken and real votes cast to `lpB` are undercounted.

### Citations

**File:** wombat/WombatBribeManager.sol (L208-216)
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

**File:** wombat/WombatBribeManager.sol (L246-268)
```text
        uint256 length = pools.length;
        address[] memory _pools = new address[](length);
        int256[] memory votes = new int256[](length);
        address[] memory rewarders = new address[](length);

        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[pools[i]];
            _pools[i] = pool.poolAddress;
            rewarders[i] = pool.rewarder;

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

**File:** rewards/BribeRewardPool.sol (L72-85)
```text
    function withdrawFor(
        address _for,
        uint256 _amount,
        bool claim
    ) external virtual onlyOperator updateRewards(_for, rewardTokens) {
        totalSupply = totalSupply - _amount;
        _balances[_for] = _balances[_for] - _amount;

        emit Withdrawn(_for, _amount);

        if (claim) {
            _getReward(_for);
        }
    }
```

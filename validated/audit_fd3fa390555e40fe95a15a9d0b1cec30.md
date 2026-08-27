### Title
`unvote()` permanently freezes vote-weight and unclaimed bribe yield after `addPool()` re-registers a pool with a new rewarder - ([File: wombat/WombatBribeManager.sol])

### Summary
`unvote()` blindly trusts `poolInfos[_lp].rewarder` when calling `withdrawFor`, but this value can be overwritten by the owner via `addPool()` while a user still has an outstanding balance recorded in the *old* rewarder contract. Once overwritten, the user's `unvote()` (and follow-up `vote()` with a negative delta) calls hit the *new* rewarder, which has no record of the user's stake, causing an arithmetic underflow revert in `withdrawFor`, permanently trapping the user's vote weight and bribe rewards accrued in the old rewarder.

### Finding Description
`vote()` records a user's staked vlMGP against a pool by calling `IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta))` [1](#0-0) , and increments `userVotedForPoolInVlmgp[msg.sender][pool.poolAddress]`. This mapping is keyed only by pool address, not by rewarder.

`addPool()` is callable by the owner and unconditionally overwrites `poolInfos[_lp]`, including `rewarder`, with no check for whether the pool already exists or has an outstanding `totalVoteInVlmgp`/user balances: [2](#0-1) 

When `unvote()` is later called, it reads the *current* `pool.rewarder` (now R2) and passes the user's `userVotedForPoolInVlmgp` amount (X, accrued against R1) into `withdrawFor` on R2: [3](#0-2) 

`BribeRewardPool.withdrawFor` performs unchecked-style Solidity 0.8 arithmetic on the rewarder's own local `_balances` mapping: [4](#0-3) 

Since R2 has never had `stakeFor` called for this user (`_balances[_for] == 0` on R2), `_balances[_for] - _amount` underflows and reverts. `vote()` with a negative delta suffers the identical failure mode, since it also targets `pool.rewarder` (now R2) for `withdrawFor` [5](#0-4) . There is no code path that lets the user reach R1 directly (bribe manager is the only caller since `withdrawFor`/`stakeFor` are `onlyOperator`-gated, and `operator` is the bribe manager/staking contract).

Consequently:
- The user's `userVotedForPoolInVlmgp[user][P]` entry can never be zeroed out through the bribe manager.
- `userTotalVotedInVlmgp[user]` remains inflated by X forever, permanently reducing the user's future votable capacity (`getUserVotable`).
- Bribe rewards continuing to accrue for the user's stake left in R1 (`_balances[user]` on R1 is still X, untouched) become permanently unclaimable, because `_claimBribeFor`/`previewBribes` also resolve the rewarder through `poolInfos[_lp].rewarder`, which now points at R2 [6](#0-5) [7](#0-6) .

No existing modifier or check prevents this: `unvote()` only checks `pool.isActive`, which is `true` again immediately after `addPool()` re-adds the pool [8](#0-7) .

### Impact Explanation
This results in permanent freezing of the user's unclaimed bribe yield accrued in the stale rewarder (R1), and a permanent, un-recoverable reduction of the user's voting capacity in the bribe manager (their vlMGP vote allocation is stuck against a pool they can never unvote from through normal means). This matches the Immunefi impact class "permanent freezing of funds" / "theft or permanent freezing of unclaimed yield," satisfying the ≥24h freeze scope since there is no owner-side remediation path modeled in this contract that restores R1 as `pool.rewarder` for the affected user's withdrawal.

### Likelihood Explanation
The attacker only needs to be a normal user who voted for a pool before the owner (an unprivileged party from the attacker's perspective, but a real operational action - re-registering a pool's rewarder via `addPool` - is a plausible, intended admin operation, not a malicious admin action) calls `addPool` again on the same `_lp` with a different `_rewarder`. This is a foreseeable admin workflow (e.g., migrating/upgrading a rewarder contract) rather than malicious governance, and the resulting fund-freeze is a direct consequence of missing safety checks in `addPool`/`unvote`, not of admin malice. Any user who voted before the rewarder swap and has not unvoted beforehand is affected; no special capital or timing is needed by the attacker beyond having voted.

### Recommendation
- In `addPool()`, prevent silently overwriting an existing pool's rewarder while `poolInfos[_lp].totalVoteInVlmgp > 0` or migrate outstanding stakes atomically (e.g., require `removePool`/full unvote-and-drain of the old rewarder before allowing a rewarder change, or add an explicit `changeRewarder()` function that transfers per-user balances from old to new rewarder before switching `poolInfos[_lp].rewarder`).
- Alternatively, track the rewarder used at vote time per user (e.g. `mapping(address => mapping(address => address)) userPoolRewarder`) so `unvote()` always targets the rewarder the stake was actually recorded in.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `WombatBribeManager`, two `BribeRewardPool` instances R1 and R2 for the same LP `P`, and set `operator` on both to the bribe manager/staking contract.
2. Owner calls `addPool(P, R1, "pool")`.
3. Attacker calls `vote([P], [+X])`; assert `IBribeRewardPool(R1).balanceOf(attacker) == X` and `userVotedForPoolInVlmgp[attacker][P] == X`.
4. Owner calls `addPool(P, R2, "pool")` again (simulating a rewarder migration), overwriting `poolInfos[P].rewarder` to R2.
5. Attacker calls `unvote(P)`.
6. Assert the call reverts (arithmetic underflow) because `IBribeRewardPool(R2).balanceOf(attacker) == 0 < X`.
7. Assert `userVotedForPoolInVlmgp[attacker][P]` is still X and `userTotalVotedInVlmgp[attacker]` still includes X, proving the vote weight is permanently stuck.
8. Optionally simulate reward accrual on R1 (`queueNewRewards`) and show `previewBribes(P, attacker)` reads through R2 (returns 0/incorrect data), demonstrating unclaimed yield in R1 is now unreachable via the bribe manager.

### Citations

**File:** wombat/WombatBribeManager.sol (L168-176)
```text
    /// @notice Returns pending bribes
    function previewBribes(
        address _lp,
        address _for
    ) external view returns (address[] memory rewardTokens, uint256[] memory amounts) {
        Pool storage pool = poolInfos[_lp];
        (rewardTokens, ) = IBribeRewardPool(pool.rewarder).rewardTokenInfos();
        amounts = IBribeRewardPool(pool.rewarder).allEarned(_for);
    }
```

**File:** wombat/WombatBribeManager.sol (L196-199)
```text
                if (delta > 0) {
                    pool.totalVoteInVlmgp += uint256(delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] += uint256(delta);
                    IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta));
```

**File:** wombat/WombatBribeManager.sol (L200-204)
```text
                } else {
                    pool.totalVoteInVlmgp -= uint256(-delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] -= uint256(-delta);
                    IBribeRewardPool(pool.rewarder).withdrawFor(msg.sender, uint256(-delta), false);
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

**File:** wombat/WombatBribeManager.sol (L399-406)
```text
    /// @notice Harvests user rewards for each pool
    /// @notice If bribes weren't harvested, this might be lower than actual current value
    function _claimBribeFor(address[] calldata lps, address _for) internal {
        uint256 length = lps.length;
        for (uint256 i; i < length; i++) {
            IBribeRewardPool(poolInfos[lps[i]].rewarder).getReward(_for, _for);
        }
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

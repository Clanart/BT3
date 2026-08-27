### Title
`unvote()` cannot be called on a pool marked inactive, permanently freezing users' voting power and unclaimed bribes - (`File: wombat/WombatBribeManager.sol`)

### Summary
`WombatBribeManager.unvote()` is documented as the mechanism that lets a user withdraw votes from a pool that has been deactivated so that "deleting a pool ... doesn't block users from withdrawing," but its guard condition is inverted: it reverts precisely when the pool is inactive, i.e. exactly the case it is supposed to handle.

### Finding Description
`vote()` requires `pool.isActive` to be true for every pool touched, so once a pool's `isActive` flag is turned off, ordinary users can no longer add or remove votes for it through `vote()`: [1](#0-0) 

The dedicated escape hatch, `unvote()`, is explicitly commented as being meant to let users unvote from an *inactive* pool, but its `require`-equivalent check is the same as `vote()`'s — it reverts `PoolNotActive` when the pool is **not** active, which is the inverse of the documented intent: [2](#0-1) 

As a result, once a pool is deactivated, a user who had voted for it has no code path to reduce `userVotedForPoolInVlmgp[msg.sender][pool.poolAddress]`, `pool.totalVoteInVlmgp`, or `userTotalVotedInVlmgp[msg.sender]` for that pool: `vote()` reverts (isActive check) and `unvote()` also reverts (inverted isActive check). The user's staked balance inside the pool's `BribeRewardPool` (`stakeFor`/`withdrawFor`, gated by `onlyOperator`) likewise becomes permanently un-adjustable from the user side: [3](#0-2) 

This mirrors the `killGauge()` bug class from the referenced report: deactivating a voting target (a normal, expected operational action) is not accompanied by consistent bookkeeping/withdrawal logic for the users who had weight allocated to it, leaving stale accounting entries (`totalVoteInVlmgp`, `userTotalVotedInVlmgp`) that the user cannot unwind, and that continue to be summed into `totalVlMgpInVote` and used by `castVotes()` when redistributing votes/bribes: [4](#0-3) 

### Impact Explanation
The user's `userTotalVotedInVlmgp[msg.sender]` amount tied to the dead pool remains permanently locked and unusable: it cannot be re-voted to another active pool (since `getUserVotable` continues to count it as spent, and `vote()`/`unvote()` both revert for the dead pool), so the user permanently loses the ability to redirect that voting weight to earn bribes elsewhere. This is a permanent freezing of the user's voting allocation and forfeited unclaimed bribe yield tied to that allocation, satisfying the "permanent freezing of unclaimed yield" / 24h+ freeze bar, without requiring any malicious admin action — deactivating a pool is normal, expected protocol maintenance (as the original judge noted for `killGauge`).

### Likelihood Explanation
Likelihood is moderate-to-high: pool deactivation is a routine, expected lifecycle event for LP/gauge pools (pools get delisted, migrated, or have rewarders changed over the life of the protocol), and any user who has voted for that specific pool at the time of deactivation is affected with no available remediation path in the current code.

### Recommendation
Fix the inverted guard in `unvote()` so it only permits unvoting when the pool is inactive (matching the function's documented purpose), while still allowing active-pool unvoting via `vote()` with a negative delta. Alternatively, remove the `isActive` restriction from `unvote()` entirely so it always allows a user to exit their position regardless of pool state, and ensure `castVotes()`/`totalVlMgpInVote` bookkeeping is kept consistent whenever a pool's `isActive` flag is flipped.

### Proof of Concept
1. User calls `vote([lpA], [1000e18])` while `poolInfos[lpA].isActive == true`; this sets `userVotedForPoolInVlmgp[user][lpA] = 1000e18`, `poolInfos[lpA].totalVoteInVlmgp += 1000e18`, `userTotalVotedInVlmgp[user] += 1000e18`, and stakes into `lpA`'s `BribeRewardPool`. [5](#0-4) 
2. Protocol admin deactivates the pool (sets `poolInfos[lpA].isActive = false`), a normal operational action analogous to `killGauge()`.
3. User attempts `vote([lpA], [-1000e18])` to reclaim voting weight → reverts with `PoolNotActive` because `vote()` requires `isActive == true`. [6](#0-5) 
4. User attempts `unvote(lpA)`, the documented remedy for exactly this situation → also reverts with `PoolNotActive` because the check is inverted (`if (!pool.isActive) revert`). [7](#0-6) 
5. The user's 1000e18 voting weight and its corresponding entry in `userTotalVotedInVlmgp[user]` is now permanently stuck: it cannot be withdrawn, reallocated, or used to earn bribes on any active pool, for as long as the pool remains inactive (which, absent a reactivation, is indefinite).

### Citations

**File:** wombat/WombatBribeManager.sol (L182-206)
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

**File:** wombat/WombatBribeManager.sol (L239-269)
```text
    /// @notice cast all pending votes
    /// @notice this  function will be gas intensive, hence a fee is given to the caller
    function castVotes(bool swapForBnb)
        override public
        returns (address[][] memory finalRewardTokens, uint256[][] memory finalFeeAmounts)
    {
        lastCastTime = block.timestamp;
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
        }
```

**File:** rewards/BribeRewardPool.sol (L57-85)
```text
    function stakeFor(address _for, uint256 _amount)
        external
        virtual
        onlyOperator
        updateRewards(_for, rewardTokens)
    {
        totalSupply = totalSupply + _amount;
        _balances[_for] = _balances[_for] + _amount;

        emit Staked(_for, _amount);
    }

    /// @notice Updates informaiton for a user in case of a withdraw. Can only be called by the Masterchief operator
    /// @param _for Address account
    /// @param _amount Amount of withdrawed tokens by the user on masterchief
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

### Title
`unvote()` inverted active-pool check permanently traps user vote weight and locked MGP - ([File: wombat/WombatBribeManager.sol])

### Summary
`WombatBribeManager.unvote()` is documented to let a user pull their vote allocation from a pool that has been deactivated ("This makes it so that deleting a pool, or changing a rewarder doesn't block users from withdrawing"), but the implemented guard does the opposite: it reverts precisely when the pool is inactive, which is the one case the function is supposed to handle.

### Finding Description
`unvote(address _lp)` reads the pool's stored state and then checks: [1](#0-0) 
```
/// @notice Unvote from an inactive pool. This makes it so that deleting a pool, or changing a rewarder doesn't block users from withdrawing
function unvote(address _lp) public {
    Pool storage pool = poolInfos[_lp];
    uint256 currentVote = userVotedForPoolInVlmgp[msg.sender][pool.poolAddress];
    if(!pool.isActive)
        revert PoolNotActive();
    ...
```
The NatSpec explicitly states the purpose of this function is to unvote from an *inactive* pool once it has been deleted/deactivated, so that a user's votes aren't permanently stuck tied to a pool that no longer exists in the active set. However, the guard condition is `if(!pool.isActive) revert PoolNotActive();` — this reverts exactly when the pool is inactive, the opposite of the documented intent. As written, a user can only call `unvote` on pools that are *still active*, and is blocked from unvoting once the pool they voted for becomes inactive.

This directly ties into `vote()`, which tracks `userTotalVotedInVlmgp[msg.sender]` and enforces `userTotalVotedInVlmgp[msg.sender] <= getUserVotable(msg.sender)` (based on `vlMGP` locked balance): [2](#0-1) 

and into `VLMGP.startUnlock()`, which prevents a user from unlocking MGP below their currently-voted amount: [3](#0-2) 
```
function startUnlock(uint256 _amountToCoolDown) external override whenNotPaused nonReentrant {
    if (_amountToCoolDown > getUserTotalLocked(msg.sender))
        revert NotEnoughLockedMPG();
    uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
    if (address(wombatBribeManager) != address(0) && 
        totalLockAfterStartUnlock < IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender))
        revert NotEnoughLockedMPG();
```

Because `unvote()` cannot be called once the target pool is deactivated, an ordinary vlMGP holder who has voted for a pool that later becomes inactive has no unprivileged path to reduce `userTotalVotedInVlmgp[msg.sender]` for that allocation. The vote weight tied to that inactive pool becomes permanently stuck, and any locked MGP corresponding to that voted amount can never be unlocked via `startUnlock`, since the check in `VLMGP.startUnlock` will always fail for that portion.

### Impact Explanation
This results in permanent freezing of the user's own locked MGP tokens (the vote-locked balance tied to the now-inactive pool can never be reduced or unlocked) — an ordinary, unprivileged wallet action (voting, followed by a pool later being deactivated in the normal course of protocol operation) leads to funds being permanently unrecoverable through the intended unlock/unvote flow. This satisfies the "permanent freezing of funds" and "24-hour-plus freeze" impact bar, since there is no unprivileged code path to reverse it once the pool is deactivated.

### Likelihood Explanation
The bug is triggered under completely normal, expected operational conditions: any pool naturally can be deactivated/removed over the protocol's lifetime (rewarders change, LPs get delisted, etc.), and any user who voted for that pool prior to deactivation is affected — no privileged or malicious behavior is required to trigger the freeze, only ordinary passage of protocol lifecycle events combined with a user's own past vote.

### Recommendation
Fix the inverted condition in `unvote()` so it explicitly targets inactive pools (or removes the restriction entirely so unvoting is always possible for the caller's own recorded vote), e.g.:
```solidity
function unvote(address _lp) public {
    Pool storage pool = poolInfos[_lp];
    uint256 currentVote = userVotedForPoolInVlmgp[msg.sender][pool.poolAddress];
    if (pool.isActive)
        revert PoolStillActive(); // or simply drop the isActive gate entirely
    ...
```
Additionally verify downstream calls (`IBribeRewardPool(pool.rewarder).withdrawFor(...)`) still function correctly against a deactivated pool's rewarder so the full unvote flow succeeds.

### Proof of Concept
1. User locks MGP in `VLMGP` and calls `WombatBribeManager.vote()` to allocate some of their vlMGP voting power to pool `P`, incrementing `userTotalVotedInVlmgp[user]`. [4](#0-3) 
2. Protocol owner later deactivates pool `P` (normal maintenance, e.g. sets `poolInfos[P].isActive = false`) — not itself a malicious act, simply part of standard pool lifecycle.
3. User attempts to call `unvote(P)` to release their vote allocation back — the call reverts with `PoolNotActive()` because `pool.isActive` is `false`, exactly the opposite of the documented behavior. [5](#0-4) 
4. User's `userTotalVotedInVlmgp[user]` amount tied to `P` can never be decremented via any unprivileged call.
5. User calls `VLMGP.startUnlock()` trying to unlock MGP beyond `totalLocked - userTotalVotedInVlmgp`; it reverts with `NotEnoughLockedMPG()` for that portion indefinitely. [3](#0-2) 

Result: the user's MGP corresponding to the stuck vote allocation is permanently frozen, with no available unprivileged remediation path.

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

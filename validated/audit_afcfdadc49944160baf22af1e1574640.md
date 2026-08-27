### Title
`Airdrop.updateEndRemainingAllocation()` is unguarded and callable directly to re-snapshot `totalEndRemainingAllocation` after other users have claimed, letting an attacker steal a disproportionate share of `totalBonus` - (File: rewards/Airdrop.sol)

### Summary
`updateEndRemainingAllocation()` is a `public` function with no access control and no guard preventing re-execution once `totalEndRemainingAllocation` has already been set. [1](#0-0)  While `claim()` only auto-invokes it once (guarded by `if (totalEndRemainingAllocation == 0)`), any unprivileged address can call the public function directly at any later time to overwrite the bonus denominator with the *current* (shrunk) `totalRemainingAllocation`, after other claimants have already reduced it. [2](#0-1) 

### Finding Description
The bonus for late claimants is computed as:
```
bonusAmount = userAllocation * totalBonus / totalEndRemainingAllocation
``` [3](#0-2) 

`totalEndRemainingAllocation` is meant to be a one-time snapshot of `totalRemainingAllocation` taken at `periodsEndTime[4]`, so that every late claimant's bonus share is calculated against the same fixed denominator (their allocation as a fraction of the total unclaimed pool at vesting completion). The only place this snapshot is supposed to happen exactly once is inside `claim()`, which checks `totalEndRemainingAllocation == 0` before calling `updateEndRemainingAllocation()`. [2](#0-1) 

However, `updateEndRemainingAllocation()` itself has no such guard - it unconditionally sets `totalEndRemainingAllocation = totalRemainingAllocation` whenever `block.timestamp >= periodsEndTime[4]`, and it is `public`, so anyone can call it directly outside of `claim()`. [4](#0-3) 

Each `claim()` call reduces `totalRemainingAllocation` by the claimant's `userAllocation`. [5](#0-4)  An attacker who is themselves a legitimate remaining-allocation holder can wait until most other remaining allocation holders have claimed (each decrementing `totalRemainingAllocation` while `totalEndRemainingAllocation` stays frozen at the original snapshot for everyone else's fair calculation). Once `totalRemainingAllocation` has collapsed down to a value close to the attacker's own allocation, the attacker calls `updateEndRemainingAllocation()` directly, forcing `totalEndRemainingAllocation` down to this new, much smaller value. The attacker then calls `claim()`; since `totalEndRemainingAllocation` is now non-zero, the `if` check in `claim()` is skipped and the manipulated denominator is used to compute the attacker's bonus, which is now `userAllocation * totalBonus / (a value close to userAllocation)` ≈ `totalBonus` - effectively the attacker captures nearly the *entire* forfeited bonus pool instead of their fair pro-rata share.

### Impact Explanation
`totalBonus` is a shared pool of forfeited allocations from early claimants, intended to be split pro-rata among all late/full-term claimants based on their share of the total remaining allocation at vesting end. [6](#0-5)  By manipulating `totalEndRemainingAllocation` after other participants have already claimed against the original snapshot, the attacker extracts far more than their fair share of `totalBonus`, directly funded by `aidropToken` held in the contract - tokens that were earmarked for other claimants or the owner's dust recovery. This is a direct, quantifiable theft of pooled reward funds via a public function with no access control, matching Critical - Direct theft of user funds.

### Likelihood Explanation
The attack requires no privileged role and no capital beyond holding a normal, registered allocation (a precondition already satisfied by any eligible participant). [7](#0-6)  It only requires timing: waiting for `block.timestamp >= periodsEndTime[4]` and for other allocation holders to claim first, then calling the unguarded `updateEndRemainingAllocation()` before the attacker's own `claim()`. This is trivially repeatable/observable on-chain (allocations and claim events are public) and requires only two ordinary transactions.

### Recommendation
Add a guard inside `updateEndRemainingAllocation()` itself (not just in the `claim()` call site) so the snapshot can only be taken once, e.g. `require(totalEndRemainingAllocation == 0, "already set")`, or restrict the function to internal/owner-only invocation and remove public re-entry into the snapshot logic.

### Proof of Concept
Foundry test plan:
1. Deploy `Airdrop` with `startTime = T`; register 3 users: A (large allocation), B (large allocation), Attacker (small allocation), and fund the contract with `aidropToken`.
2. Warp to `periodsEndTime[4]`. Have some earlier claims happen pre-period-4 by other addresses to populate `totalBonus` via forfeiture (`claimableAmount < userAllocation`).
3. Have user A call `claim()` first → this auto-triggers `updateEndRemainingAllocation()`, snapshotting `totalEndRemainingAllocation = totalRemainingAllocation` (large value including A, B, Attacker allocations).
4. Have user B call `claim()` → receives bonus based on original large snapshot; `totalRemainingAllocation` shrinks to ≈ Attacker's allocation only.
5. Attacker calls `updateEndRemainingAllocation()` directly (not via `claim()`) → `totalEndRemainingAllocation` collapses to ≈ Attacker's own remaining allocation.
6. Attacker calls `claim()` → assert `bonusAmount` received ≈ full `totalBonus`, vastly exceeding the fair pro-rata share (`attackerAllocation/originalTotalRemainingAllocation * totalBonus`).
7. Assert `totalEndRemainingAllocation != totalRemainingAllocation` reconciliation invariant is broken, and that the attacker's net token balance increase exceeds their entitled share, while contract's remaining balance available for legitimate future/other claimants is reduced or insufficient (`InsufficientBalance` revert path triggered for others).

### Citations

**File:** rewards/Airdrop.sol (L63-78)
```text
    function register(
        address[] calldata _addresses,
        uint256[] calldata _amounts
    ) external onlyOwner {
        if(_addresses.length != _amounts.length) revert RewardNotMatch();
        if(block.timestamp >= startTime) revert AlreadyStarted();

        uint256 length = _addresses.length;
        for (uint256 i = 0; i < length; i++) {
            if(_addresses[i] == address(0)) revert NotAllowZeroAddress();
            if(allocations[_addresses[i]] > 0)
                totalRemainingAllocation -= allocations[_addresses[i]];
            allocations[_addresses[i]] = _amounts[i];
            totalRemainingAllocation += _amounts[i];
        }
    }
```

**File:** rewards/Airdrop.sol (L127-143)
```text
    function getBonusAmount(address _user)
        public
        view
        returns (uint256 bonusAmount)
    {
        bonusAmount = 0;
        uint256 userAllocation = allocations[_user];
        if (
            block.timestamp >= periodsEndTime[4] &&
            totalEndRemainingAllocation != 0
        ) {
            bonusAmount =
                ((userAllocation * 10**9) * totalBonus) /
                totalEndRemainingAllocation /
                10**9;
        }
    }
```

**File:** rewards/Airdrop.sol (L145-150)
```text
    /// @notice This will store the ending remaining amount for the bonus.
    function updateEndRemainingAllocation() public {
        if (block.timestamp >= periodsEndTime[4]) {
            totalEndRemainingAllocation = totalRemainingAllocation;
        }
    }
```

**File:** rewards/Airdrop.sol (L153-156)
```text
    function claim() external {
        if (totalEndRemainingAllocation == 0) {
            updateEndRemainingAllocation();
        }
```

**File:** rewards/Airdrop.sol (L162-164)
```text
        uint256 userAllocation = allocations[msg.sender];
        allocations[msg.sender] = 0;
        totalRemainingAllocation -= userAllocation;
```

**File:** rewards/Airdrop.sol (L165-168)
```text
        if (claimableAmount <= userAllocation) {
            uint256 forfeited = userAllocation - claimableAmount;
            totalBonus += forfeited;
        }
```

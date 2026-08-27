### Title
`Airdrop.claim()` uses an unguarded, repeatable `updateEndRemainingAllocation()` snapshot, allowing bonus-pool manipulation and theft of other users' unclaimed yield - (File: rewards/Airdrop.sol)

### Summary
`claim()` only invokes `updateEndRemainingAllocation()` automatically `if (totalEndRemainingAllocation == 0)` [1](#0-0) , but `updateEndRemainingAllocation()` itself is a `public` function with no access control and no re-entry guard, so anyone can call it directly and repeatedly, at any time after `periodsEndTime[4]`, re-overwriting `totalEndRemainingAllocation` to the then-current `totalRemainingAllocation` [2](#0-1) . Because `totalRemainingAllocation` shrinks monotonically as users claim (regardless of period) [3](#0-2) , an unprivileged actor can wait until many other users have claimed, then call `updateEndRemainingAllocation()` immediately before their own `claim()` to shrink the bonus denominator and capture a disproportionate share of the fixed `totalBonus` pool [4](#0-3) .

### Finding Description
`getBonusAmount()` splits the accumulated `totalBonus` (forfeitures from users who claim late and lose earlier-period shares) among period-4 claimants proportional to `userAllocation / totalEndRemainingAllocation` [5](#0-4) . This design implicitly assumes `totalEndRemainingAllocation` is a single, fixed snapshot of "total unclaimed allocation as of the start of period 4," taken once. 

The automatic path enforces "once" via `if (totalEndRemainingAllocation == 0)` inside `claim()` [1](#0-0) , but the manual/public path, `updateEndRemainingAllocation()`, has no such guard - it will overwrite `totalEndRemainingAllocation` on every call as long as `block.timestamp >= periodsEndTime[4]` [6](#0-5) . There is no role check (`onlyOwner`, etc.) on this function.

Exploit flow (two calls, can be bundled atomically in a single attacker-controlled transaction/contract):
1. Wait until `block.timestamp >= periodsEndTime[4]` and until a substantial number of other users have already called `claim()`, shrinking `totalRemainingAllocation` while `totalBonus` has already accumulated forfeitures.
2. Attacker (who still holds an unclaimed `allocations[attacker]`) calls `updateEndRemainingAllocation()` directly, re-snapshotting `totalEndRemainingAllocation` to the now much smaller `totalRemainingAllocation`.
3. Attacker immediately calls `claim()`. Since `totalEndRemainingAllocation != 0`, the automatic branch is skipped, and `getBonusAmount()` uses attacker's manipulated (small) denominator, inflating `bonusAmount` far beyond attacker's fair proportional share of the bonus pool.

This directly reduces the token balance available for legitimate remaining claimants, potentially causing their subsequent `claim()` calls to revert with `InsufficientBalance` [7](#0-6) , permanently or for an extended period freezing/denying their rightful bonus and possibly even principal allocation.

### Impact Explanation
Any unprivileged holder of a registered allocation can extract more than their fair share of the pooled `totalBonus`, at the direct expense of other legitimate claimants, and can drive the contract into a state where later claimants' `claim()` reverts due to insufficient balance. This matches "Critical - Direct theft of user funds" / theft of unclaimed yield, since it is a direct, quantifiable transfer of value from honest claimants to the attacker via manipulation of a public accounting variable that should be immutable once fixed.

### Likelihood Explanation
No special role or capital is required — `updateEndRemainingAllocation()` and `claim()` are both `external`/`public` with no `onlyOwner`/`nonReentrant` restriction [8](#0-7) . The only precondition is holding an `allocations[msg.sender] > 0` (achieved via being a legitimately registered address) and waiting past `periodsEndTime[4]` while other users claim — both realistic, low-cost, and repeatable conditions.

### Recommendation
Add a one-shot guard to `updateEndRemainingAllocation()` itself (e.g., `if (totalEndRemainingAllocation != 0) return;` or track a separate boolean flag) so the snapshot can only ever be taken once, consistent with the intent of the `claim()` auto-path, and consider restricting the manual call to prevent opportunistic timing by any single unprivileged actor:
```solidity
function updateEndRemainingAllocation() public {
    if (totalEndRemainingAllocation != 0) return;
    if (block.timestamp >= periodsEndTime[4]) {
        totalEndRemainingAllocation = totalRemainingAllocation;
    }
}
```

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `Airdrop` with `startTime = now + 1`, register several addresses (`userA`, `userB`, attacker) with allocations, advance time past `startTime`.
2. Advance time to `periodsEndTime[4]`.
3. Have `userA` and `userB` call `claim()` normally (this triggers the auto-path once and sets `totalEndRemainingAllocation` to the then-current `totalRemainingAllocation`, and accrues `totalBonus`).
4. Reset state in a second scenario: instead, before any of `userA`/`userB` claim, have the attacker's contract call `updateEndRemainingAllocation()` and `claim()` back-to-back at a moment engineered so `totalRemainingAllocation` is minimal (e.g., after most users already claimed in the "normal" flow), and compare attacker's `bonusAmount` against the amount a fair/consistent one-shot snapshot would have produced.
5. Assert: attacker's realized `bonusAmount` exceeds `userAllocation_attacker / totalRemainingAllocation_at_period4_start * totalBonus_final` (the fair share), and/or assert that a subsequent legitimate claimant's `claim()` reverts with `InsufficientBalance` due to the depleted token balance.

### Citations

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

**File:** rewards/Airdrop.sol (L145-156)
```text
    /// @notice This will store the ending remaining amount for the bonus.
    function updateEndRemainingAllocation() public {
        if (block.timestamp >= periodsEndTime[4]) {
            totalEndRemainingAllocation = totalRemainingAllocation;
        }
    }

    /// @notice Claim MGP and forfeit remaining allocation
    function claim() external {
        if (totalEndRemainingAllocation == 0) {
            updateEndRemainingAllocation();
        }
```

**File:** rewards/Airdrop.sol (L159-160)
```text
        if(claimableAmount == 0) revert NothingToClaim();
        if(claimableAmount > aidropToken.balanceOf(address(this))) revert InsufficientBalance();
```

**File:** rewards/Airdrop.sol (L162-164)
```text
        uint256 userAllocation = allocations[msg.sender];
        allocations[msg.sender] = 0;
        totalRemainingAllocation -= userAllocation;
```

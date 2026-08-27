## Title
Unguarded `updateEndRemainingAllocation()` allows re-snapshotting the bonus denominator to inflate an attacker's `getBonusAmount()` share beyond `totalBonus` - (File: rewards/Airdrop.sol)

## Summary
`updateEndRemainingAllocation()` is a public function with no re-entry/one-time guard: every call after `periodsEndTime[4]` unconditionally overwrites `totalEndRemainingAllocation = totalRemainingAllocation` with whatever the *current* value is. Because `totalRemainingAllocation` strictly decreases every time anyone claims (pre- or post-deadline), an attacker can wait until several honest post-deadline claimants have already been paid their bonus share using the original (larger, correct) denominator, then call `updateEndRemainingAllocation()` again to shrink the denominator before their own `claim()`, inflating their own `getBonusAmount()` on the same, already-largely-distributed `totalBonus` pool.

## Finding Description
The relevant code is: [1](#0-0) 

`totalBonus` accumulates forfeited (unvested) amounts only from users who `claim()` **before** `periodsEndTime[4]`, and stops growing once the deadline passes (post-deadline claims always give 100% of `userAllocation` plus bonus, so `claimableAmount <= userAllocation` never triggers the forfeit branch). `getBonusAmount()` splits this fixed `totalBonus` pool proportionally to `userAllocation / totalEndRemainingAllocation`: [2](#0-1) 

The intended design is: `totalEndRemainingAllocation` should be snapshotted **exactly once**, capturing the sum of allocations of exactly those users who are yet to claim as of `periodsEndTime[4]`, so that `sum(bonusAmount) == totalBonus` when every remaining holder eventually claims. Inside `claim()` the snapshot is guarded (`if (totalEndRemainingAllocation == 0)`), but `updateEndRemainingAllocation()` itself has no such guard:
```
function updateEndRemainingAllocation() public {
    if (block.timestamp >= periodsEndTime[4]) {
        totalEndRemainingAllocation = totalRemainingAllocation;
    }
}
```
Since it is `public` and callable by anyone at any time after the deadline, and `totalRemainingAllocation` is reduced by every subsequent `claim()` (regardless of whether that claim happens before or after the deadline), calling it again after some post-deadline claims have already occurred re-snapshots the denominator to a smaller value — one that no longer reflects the population over which `totalBonus` was actually meant to be split. A subsequent claimant (the attacker) then computes `bonusAmount = userAllocation * totalBonus / (smaller denominator)`, receiving a share that, combined with the shares already paid to earlier honest post-deadline claimants (computed against the original larger denominator), causes total bonus payouts to exceed `totalBonus`.

No modifier, `nonReentrant` guard, or accounting check prevents this, because `updateEndRemainingAllocation()` is deliberately exposed as a standalone `public` helper without a "set-once" flag of its own.

Note: the literal framing in the question (attacker "front-running" the very first snapshot to permanently fix it at the maximum, or merely delaying their own `claim()` past other pre-deadline forfeitures) does **not** by itself break conservation — `totalBonus` stops growing after `periodsEndTime[4]`, and excluding already-forfeited early claimers from the denominator is by design (they knowingly get no bonus). The actual exploitable defect is the missing one-time-set guard on `updateEndRemainingAllocation()` allowing repeated re-snapshotting after post-deadline claims have already been paid out, which does break the stated conservation invariant.

## Impact Explanation
An unprivileged attacker who is a registered allocation holder can, at no privileged cost, call the public `updateEndRemainingAllocation()` after observing other users claim post-deadline, shrinking the bonus denominator immediately before their own `claim()`. This lets the attacker capture a disproportionate share of the shared `totalBonus` pool, causing aggregate bonus payouts to exceed `totalBonus` — i.e., theft of unclaimed/shared yield from other allocation holders, matching the "theft or permanent freezing of unclaimed yield" impact class.

## Likelihood Explanation
Preconditions are minimal: the attacker only needs a registered `allocations[attacker]` entry (the same precondition stated in the question) and to wait for at least one honest post-deadline claim to occur before their own claim, then call the public, unguarded `updateEndRemainingAllocation()` themselves. No special capital, flash loans, or privileged roles are required, and the attack is repeatable by any attacker with an allocation, for as long as `totalRemainingAllocation` keeps shrinking (i.e., as long as other users keep claiming).

## Recommendation
Add a one-time-set guard directly inside `updateEndRemainingAllocation()` (mirroring the check currently only present in `claim()`), e.g. `if (totalEndRemainingAllocation != 0) return;`, so the snapshot can only ever be taken once, regardless of who or how many times the function is called.

## Proof of Concept
Foundry test outline:
1. Deploy `Airdrop` with `startTime = now + 1`, register Alice (300), Bob (300), Carol (300), Attacker (100); total = 1000.
2. Warp to `periodsEndTime[2]`/`periodsEndTime[3]`; have Alice and Bob `claim()` early, forfeiting into `totalBonus` (`totalBonus` becomes 270, `totalRemainingAllocation` becomes 400: Carol 300 + Attacker 100).
3. Warp to `periodsEndTime[4]`; have Carol `claim()` first (this is the first call, so `totalEndRemainingAllocation` gets set to 400 via the `claim()` guard). Assert Carol receives `bonusAmount = 300*270/400 = 202`.
4. As the attacker, call `updateEndRemainingAllocation()` directly (no guard prevents re-invocation) — this resets `totalEndRemainingAllocation` to the new `totalRemainingAllocation = 100` (only attacker left).
5. Attacker calls `claim()`; assert `bonusAmount = 100*270/100 = 270` (the full pool again).
6. Assert `Carol's bonus (202) + Attacker's bonus (270) = 472 > totalBonus (270)`, i.e., aggregate bonus payouts exceed the pool, and check `aidropToken.balanceOf(address(this))` before/after to show funds paid out exceed what was reserved for the bonus pool, violating conservation.

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

**File:** rewards/Airdrop.sol (L145-170)
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
        uint256 claimableAmount = getClaimableAmount(msg.sender);
        
        if(claimableAmount == 0) revert NothingToClaim();
        if(claimableAmount > aidropToken.balanceOf(address(this))) revert InsufficientBalance();

        uint256 userAllocation = allocations[msg.sender];
        allocations[msg.sender] = 0;
        totalRemainingAllocation -= userAllocation;
        if (claimableAmount <= userAllocation) {
            uint256 forfeited = userAllocation - claimableAmount;
            totalBonus += forfeited;
        }
        aidropToken.safeTransfer(msg.sender, claimableAmount);
    }
```

### Title
Rounding-down in `_getClaimable` permanently locks part of a claimant's airdrop allocation until an extra full vesting interval elapses - (File: rewards/Airdrop2.sol)

### Summary
`Airdrop2._getClaimable` computes the vested portion of a merkle-proof airdrop allocation using the exact same double-rounding pattern flagged in the external report for `VestingMilestone.vestedAmount`: it divides before multiplying, and only corrects the resulting shortfall once the elapsed time exceeds the *full* vesting duration by an extra interval, rather than clamping to the full allocation once vesting is complete.

### Finding Description
`_getClaimable` computes:
```
vested = (totalAmount*5/100) + (totalAmount*95/100) * ((block.timestamp - startVestingTime) / intervals) / vestingPeriodCount
``` [1](#0-0) 

Due to Solidity's left-to-right evaluation of `*`/`/` at equal precedence, this is evaluated as `((totalAmount*95/100) * numIntervals) / vestingPeriodCount`, where `numIntervals = (block.timestamp - startVestingTime) / intervals` (itself floor-divided). At the exact moment vesting completes (`numIntervals == vestingPeriodCount`), the result is `totalAmount*5/100 + totalAmount*95/100` (with the `95/100` division already rounded down), which can be strictly less than `totalAmount`. This is the identical rounding pattern as `amountPerInterval * intervals` in the referenced `VestingMilestone.vestedAmount` bug.

The only safety net is:
```
if (vested > totalAmount) return totalAmount - claimed;
``` [2](#0-1) 

but this branch is only reached once `numIntervals` exceeds `vestingPeriodCount` by enough to overshoot `totalAmount` (approximately one full extra `intervals` period after 100% vesting time has elapsed) — unlike the report's remediation, which returns the full allocation as soon as `_referenceTs` passes the vesting end time.

### Impact Explanation
Once a user's vesting period completes, `claim()` and `getClaimable()` under-report the truly vested amount by a rounding remainder (up to a fraction of a percent of `totalAmount`, scaled by `totalAmount` and `vestingPeriodCount`) for as long as one additional `intervals` duration. `intervals` is set at deployment by the vesting schedule design (e.g., monthly release cadences are typical for this style of contract), so the frozen remainder can remain inaccessible to an ordinary, unprivileged claimant for well over 24 hours — a freeze of a portion of their unclaimed airdrop allocation.

### Likelihood Explanation
This triggers for every account and every full merkle-verified allocation whose `totalAmount * 95 / 100` is not evenly divisible in a way that reconciles exactly with `vestingPeriodCount`, which will be common for arbitrary allocation amounts — no attacker action is required, it fires deterministically as vesting reaches 100%.

### Recommendation
Mirror the report's remediation: in `_getClaimable`, check if `block.timestamp >= startVestingTime + vestingPeriodCount * intervals` (i.e., vesting is fully complete) and in that case return `totalAmount - claimed` directly, instead of relying on the `vested > totalAmount` overshoot check that only self-corrects after an extra interval has elapsed.

### Proof of Concept
1. Deploy `Airdrop2` with `vestingPeriodCount = 3`, `intervals = 30 days`, and a merkle root covering a user with `totalAmount = 10`.
2. At `startVestingTime + 3*intervals` (fully vested), call `claim`: `vested = 10*5/100 + (10*95/100)*3/3 = 0 + 9 = 9`, so only `9` of the `10` allocated tokens are claimable — 1 token is locked.
3. The user must wait an additional `intervals` (30 days) before `numIntervals` becomes `4`, pushing `vested` above `totalAmount` and triggering the `vested > totalAmount` branch that finally returns the full `10 - claimed`.
4. During that 30-day window, the remaining token is inaccessible to the user, matching a 24-hour-plus freeze of unclaimed distribution funds.

### Citations

**File:** rewards/Airdrop2.sol (L100-112)
```text
    function _getClaimable(address account, uint256 totalAmount) internal view returns (uint256) {
        uint256 claimed = getClaimed(account);
        if (claimed >= totalAmount) {
            return 0;
        }

        uint256 vested = (totalAmount * 5 / 100) + (totalAmount * 95 / 100) * ((block.timestamp - startVestingTime) / intervals) / (vestingPeriodCount);
        if (vested > totalAmount) {
            return totalAmount - claimed;
        }

        return vested - claimed;
    }
```

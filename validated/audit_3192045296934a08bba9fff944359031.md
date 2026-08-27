### Title
Unguarded re-snapshot of `updateEndRemainingAllocation()` allows theft of bonus tokens owed to other claimants - ([File: rewards/Airdrop.sol])

### Summary
`updateEndRemainingAllocation()` is a `public` function with no access control and no one-time guard, so any unprivileged address can call it repeatedly after `periodsEndTime[4]`. Because `totalRemainingAllocation` shrinks every time a user claims (their allocation is zeroed and subtracted), an attacker can re-trigger the snapshot after some legitimate users have already claimed, shrinking `totalEndRemainingAllocation` and inflating the bonus share paid to themselves and to whoever claims after them, at the expense of `totalBonus` conservation.

### Finding Description
The bonus math relies on a single, immutable snapshot of the "remaining pool" taken exactly once at the start of the period-4 claiming window: [1](#0-0) 

`claim()` only triggers this snapshot conditionally, guarded by `totalEndRemainingAllocation == 0`: [2](#0-1) 

but `updateEndRemainingAllocation()` itself has **no such guard and no access control** — it unconditionally overwrites `totalEndRemainingAllocation = totalRemainingAllocation` whenever called after `periodsEndTime[4]`. Since `totalRemainingAllocation` decreases every time any user calls `claim()` (line `totalRemainingAllocation -= userAllocation`), an attacker can:

1. Wait until the first honest claimant (e.g. Alice) claims at `periodsEndTime[4]`, which auto-snapshots `totalEndRemainingAllocation` to the full remaining pool (e.g. 3000, including Alice's own 1000).
2. After Alice's claim zeroes her allocation and shrinks `totalRemainingAllocation` to 2000, the attacker directly calls `updateEndRemainingAllocation()` again. Since there is no guard preventing re-invocation, this overwrites `totalEndRemainingAllocation` to the new, smaller value (2000), which no longer represents the true pool at the fair snapshot instant, and no longer sums correctly across remaining participants.
3. The attacker then calls `claim()`. `getBonusAmount()` computes `bonusAmount = userAllocation * totalBonus / totalEndRemainingAllocation` using the shrunk denominator, giving the attacker a bonus share disproportionate to their true `allocation / originalPool` ratio.
4. Any user who claims after the attacker (e.g. Bob) also benefits from the same shrunk denominator, over-paying bonus a second time on the portion that was already fairly paid out to Alice.

The root cause is that `totalEndRemainingAllocation` must represent a fixed set of "still-unclaimed-at-period4" participants whose allocations sum exactly to it; but because participants leave the pool (get zeroed) as they claim, and the function can be re-run without restriction, the invariant `sum(remaining allocations) == totalEndRemainingAllocation` is broken after the first re-trigger. This directly breaks conservation: `sum(claimableAmount) over all users` ends up exceeding `totalRegisteredPrincipal + totalBonus`, so tokens paid to the attacker (and to whoever claims immediately after the manipulation) come at the expense of later claimants, who will either receive less than owed or have their `claim()` revert with `InsufficientBalance()` once the contract balance is drained (fund freezing / theft).

No existing modifier (no `nonReentrant`, no one-time-only flag, no `onlyOwner`) prevents this re-invocation.

### Impact Explanation
This is a direct theft of user funds: the attacker extracts more than their pro-rata share of `totalBonus`, funded by tokens that rightfully belong to other registered, unclaimed users. In the worst case, the last claimant(s) in the airdrop are left with `InsufficientBalance()` reverts, permanently unable to claim their allocation once the contract's token balance is drained by earlier (attacker-manipulated) claims — a direct loss of principal/bonus for legitimate users and an economic gain for the attacker.

### Likelihood Explanation
The attack requires no privileged role, no capital beyond the attacker's own legitimate `allocations[attacker] > 0`, and is trivially repeatable by anyone calling the permissionless `updateEndRemainingAllocation()` function at any point after `periodsEndTime[4]` while other claims are still trickling in. It only requires the airdrop to reach its final period and at least one other claimant to have already claimed (shrinking the pool) before the attacker acts — a realistic and easily observable on-chain condition (front-running / just timing a call after monitoring mempool/claims).

### Recommendation
Restrict `updateEndRemainingAllocation()` so it can only set `totalEndRemainingAllocation` once (e.g., guard with `if (totalEndRemainingAllocation != 0) return;` in the function itself, matching the check already used in `claim()`), or remove the standalone public entrypoint entirely and let `claim()` be the sole trigger with the existing `== 0` guard. This ensures the snapshot is immutable and correctly represents the fixed pool at the moment period 4 begins.

### Proof of Concept
Foundry test outline:
1. Deploy `Airdrop` with `startTime = block.timestamp + 1`.
2. `register([D, Alice, Bob, Attacker], [1000, 1000, 1000, 1000])` before start.
3. Fund contract with `aidropToken` for at least 4000 + expected bonus.
4. Warp to `periodsEndTime[1]`; have `D` call `claim()` → receives 20% (200), forfeits 800 into `totalBonus` (assert `totalBonus == 800`, `totalRemainingAllocation == 3000`).
5. Warp to `periodsEndTime[4]`.
6. `Alice.claim()` → triggers auto-snapshot: assert `totalEndRemainingAllocation == 3000`; Alice receives `1000 + 1000*800/3000 = 1266`; assert `totalRemainingAllocation == 2000` afterward.
7. Attacker calls `updateEndRemainingAllocation()` directly → assert `totalEndRemainingAllocation` changed to `2000`.
8. `Attacker.claim()` → assert `bonusAmount` computed as `1000*800/2000 = 400` (vs. fair share `~266.67`), i.e., attacker over-claims by ~133 tokens.
9. `Bob.claim()` → assert Bob also receives `1000 + 400 = 1400` using the same shrunk denominator.
10. Assert `D(200) + Alice(1266) + Attacker(1400) + Bob(1400) = 4266 > 4000` (total registered principal) `+ 800` (`totalBonus`) `= 4800`... actually assert the sum of bonus components alone (`266+400+400=1066`) exceeds `totalBonus (800)`, proving conservation violation, and/or fund the contract with exactly `4000+800=4800` tokens and show that a subsequent legitimate claimant (if one existed) would revert with `InsufficientBalance()`.

### Citations

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

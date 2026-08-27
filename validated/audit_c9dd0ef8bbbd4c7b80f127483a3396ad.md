### Title
Unguarded `updateEndRemainingAllocation()` allows re-snapshotting the bonus denominator, causing bonus over-distribution and fund freezing for later claimers - (File: rewards/Airdrop.sol)

### Summary
`updateEndRemainingAllocation()` in `Airdrop.sol` is `public` with no access control and no "already-set" guard beyond the time check, so any unprivileged address can call it repeatedly after `periodsEndTime[4]` to overwrite `totalEndRemainingAllocation` with the current (monotonically decreasing) `totalRemainingAllocation`. Because `totalRemainingAllocation` decreases every time a user calls `claim()`, re-triggering this update after some users have already claimed shrinks the bonus denominator used by `getBonusAmount()`, causing the sum of bonus payouts to exceed `totalBonus` and draining principal meant for later unclaimed users.

### Finding Description
`getBonusAmount()` computes each user's bonus as:
`bonusAmount = userAllocation * totalBonus / totalEndRemainingAllocation` [1](#0-0) 

The correctness of this formula (i.e., that the sum of bonuses across all remaining allocation holders equals `totalBonus`) depends on `totalEndRemainingAllocation` being a single, fixed snapshot of `totalRemainingAllocation` taken exactly once, at the moment `periodsEndTime[4]` is reached — before anyone reduces `totalRemainingAllocation` by claiming.

However, `updateEndRemainingAllocation()` has no access control and no guard preventing repeated writes:
```
function updateEndRemainingAllocation() public {
    if (block.timestamp >= periodsEndTime[4]) {
        totalEndRemainingAllocation = totalRemainingAllocation;
    }
}
``` [2](#0-1) 

`claim()` only auto-invokes it when `totalEndRemainingAllocation == 0` (i.e., on the very first post-period-4 claim), but any address — including one with zero allocation or one that has already claimed — can call the public function directly at any later time: [3](#0-2) 

Each `claim()` reduces `totalRemainingAllocation` by the caller's forfeited allocation: [4](#0-3) 

Exploit flow:
1. After `periodsEndTime[4]`, the first claimer (e.g., victim U1) triggers the auto-snapshot: `totalEndRemainingAllocation = R0` (correct sum of all still-unclaimed allocations), and receives `a1*totalBonus/R0`.
2. U1's claim reduces `totalRemainingAllocation` to `R0 - a1`.
3. Attacker (already claimed, `allocations[attacker] = 0`, no special rights needed) calls `updateEndRemainingAllocation()` again, resetting `totalEndRemainingAllocation = R0 - a1`.
4. A colluding address (or any subsequent claimer, e.g. victim U2) now claims using the smaller denominator, receiving `a2*totalBonus/(R0-a1)`, which is strictly larger than the fair share `a2*totalBonus/R0`.
5. Summed over all remaining claimants, since their allocations sum exactly to the new denominator `(R0-a1)`, they collectively receive the *full* `totalBonus` again — on top of what U1 already received. Total bonus paid out exceeds `totalBonus` by `a1*totalBonus/R0`.

This breaks the intended conservation invariant (sum of bonus payouts == totalBonus) and pays out excess tokens that must come from the contract's remaining balance — i.e., from principal allocation earmarked for other unclaimed users. No modifier, `nonReentrant`, or "set-once" flag protects `totalEndRemainingAllocation` from re-computation, so nothing stops this griefing/self-favoring pattern.

### Impact Explanation
Later claimants can be shorted or completely blocked: as excess bonus is paid to earlier/colluding claimers, `aidropToken.balanceOf(address(this))` can be depleted below what remaining legitimate claimants are owed, triggering `InsufficientBalance()` reverts on `claim()` for the last users. This matches the Immunefi impact classes "theft of unclaimed yield" (bonus pool disproportionately captured by a colluding actor) and "permanent freezing of funds" (last legitimate claimants unable to withdraw their principal + bonus because contract balance is insufficient).

### Likelihood Explanation
No privileged role is required — `updateEndRemainingAllocation()` is `public` and callable by anyone at any time after `periodsEndTime[4]`. The only precondition is that at least one honest user has already claimed after period 4 (reducing `totalRemainingAllocation`), which will happen naturally in any real airdrop, and the attacker only needs a second (colluding) address that also has an allocation. This requires no capital beyond gas and is fully repeatable each time a new claim reduces `totalRemainingAllocation`, making it highly feasible.

### Recommendation
Make `totalEndRemainingAllocation` a true one-time snapshot: guard `updateEndRemainingAllocation()` so it can only set the value once (e.g., `if (totalEndRemainingAllocation != 0) return;` or add a boolean `endSnapshotTaken` flag), and/or restrict the function to be called only internally from `claim()`'s first invocation rather than being publicly callable at arbitrary times.

### Proof of Concept
Foundry test plan:
1. Deploy `Airdrop` with `startTime` in the future; `register()` three users U1 (a1), U2 (a2), attacker-colluder U3 (a3) with allocations before start.
2. Warp time to `>= periodsEndTime[4]`.
3. U1 calls `claim()` — assert `totalEndRemainingAllocation == a1+a2+a3` and U1 receives `a1*totalBonus/(a1+a2+a3)`.
4. Attacker (any address, possibly zero-allocation or already-claimed) calls `updateEndRemainingAllocation()` directly — assert `totalEndRemainingAllocation` updates to `a2+a3`.
5. U2 and U3 call `claim()` — assert each receives `totalBonus*a_i/(a2+a3)`, and assert `bonus(U1)+bonus(U2)+bonus(U3) > totalBonus`.
6. Assert final `aidropToken.balanceOf(address(this))` is insufficient to cover any remaining registered-but-unclaimed principal, or that a subsequent legitimate claimer reverts with `InsufficientBalance()`.

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

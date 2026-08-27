### Title
Public, un-gated `updateEndRemainingAllocation()` allows re-snapshotting the bonus denominator to steal a disproportionate share of `totalBonus` - (File: rewards/Airdrop.sol)

### Summary
`updateEndRemainingAllocation()` is `public`, carries no access control and no one-shot guard beyond the caller-side check inside `claim()`. An attacker can call it directly right before their own `claim()` call to re-snapshot `totalEndRemainingAllocation` to a smaller value (after other users have already claimed and been zeroed out of `totalRemainingAllocation`), which inflates their own `getBonusAmount()` far beyond their fair pro-rata share and causes cumulative bonus payouts to exceed `totalBonus`.

### Finding Description
The bonus mechanism intends `totalEndRemainingAllocation` to be a single, fixed denominator snapshotted once, immediately after `periodsEndTime[4]`, representing the sum of allocations of all users who have not yet claimed early (and thus are entitled to share `totalBonus` pro rata): [1](#0-0) 

`claim()` only triggers this snapshot lazily when `totalEndRemainingAllocation == 0`: [2](#0-1) 

But `updateEndRemainingAllocation()` itself has no guard preventing repeated calls — it will overwrite `totalEndRemainingAllocation` with the *current* `totalRemainingAllocation` every time it's invoked, as long as `block.timestamp >= periodsEndTime[4]`. Since `totalRemainingAllocation` decreases every time any user claims (`totalRemainingAllocation -= userAllocation` in `claim()`), calling `updateEndRemainingAllocation()` later in time yields a strictly smaller denominator than the honest, one-time snapshot.

`getBonusAmount()` uses this live, mutable value directly: [3](#0-2) 

Exploit flow:
1. After `periodsEndTime[4]`, honest users A and B (each with allocation 100, alongside attacker C with allocation 100, so `totalRemainingAllocation = 300`) call `claim()`. The first call snapshots `totalEndRemainingAllocation = 300`. Each of A and B receives `bonus = 100 * totalBonus / 300`, and `totalRemainingAllocation` drops to 100 (only C's allocation remains) after both have claimed.
2. Attacker C, in a single transaction, calls `updateEndRemainingAllocation()` directly (bypassing the `claim()`-internal `== 0` guard), which resets `totalEndRemainingAllocation = totalRemainingAllocation = 100`.
3. C then calls `claim()`. `getBonusAmount(C)` computes `bonus_C = 100 * totalBonus / 100 = totalBonus` — the attacker takes 100% of the bonus pool alone.
4. Total bonus paid out = `totalBonus/3 + totalBonus/3 + totalBonus = 5/3 * totalBonus`, exceeding the actual forfeited pool by 66%. The excess is paid out of `aidropToken` balance that should belong to remaining claimants or be reclaimable by the owner via `withdrawDust()`.

No modifier, `nonReentrant`, or access-control check stops this since `updateEndRemainingAllocation()` is `public` with only a timestamp condition.

### Impact Explanation
This directly breaks the invariant that the bonus denominator must be fixed exactly once for all participants, allowing the last-to-claim attacker to divert to themselves other users' rightful share of the forfeited-allocation pool (`totalBonus`), and can drain more tokens from the contract than were legitimately forfeited — a direct theft of user/protocol funds and a path to insolvency for later legitimate claimants who may find `aidropToken.balanceOf(address(this))` insufficient. This matches Critical - Direct theft of user funds.

### Likelihood Explanation
Fully unprivileged: any address holding an allocation (or simply any EOA, since `updateEndRemainingAllocation()` has no access restriction and can be called by literally anyone) can trigger the re-snapshot. Precondition is simply that the attacker acts after at least one other user has already claimed post-`periodsEndTime[4]` (reducing `totalRemainingAllocation`), then atomically calls `updateEndRemainingAllocation()` followed by `claim()` in the same transaction. No capital beyond gas is required, and it is repeatable by any late claimant.

### Recommendation
Add a one-shot guard directly on `updateEndRemainingAllocation()` itself (not just in `claim()`), e.g., `require(totalEndRemainingAllocation == 0, "already snapshotted")`, and/or restrict the function to be called only internally from `claim()`'s existing guard, ensuring the denominator is fixed exactly once regardless of caller.

### Proof of Concept
Foundry/Hardhat test plan:
1. Deploy `Airdrop` with `startTime = now + 1`, fund it with `aidropToken`.
2. `register([A, B, C], [100, 100, 100])`.
3. Warp to `periodsEndTime[4]`.
4. `A.claim()` → asserts `totalEndRemainingAllocation == 300`, A receives `bonus = 100*totalBonus/300` (assume `totalBonus` pre-seeded via an earlier forfeiting claimant, or seed `totalBonus` directly for the test via a prior early claimer D).
5. `B.claim()` → confirm `totalEndRemainingAllocation` unchanged at 300, B receives same bonus as A.
6. In a single transaction from attacker C: call `updateEndRemainingAllocation()` then `claim()`.
7. Assert `totalEndRemainingAllocation == 100` (re-snapshotted) and `getBonusAmount(C) == totalBonus` (100% of pool) instead of the fair `100*totalBonus/300`.
8. Assert total bonus paid (`bonus_A + bonus_B + bonus_C`) `> totalBonus`, proving overpayment/theft, and that contract's token balance is insufficient to cover remaining legitimate obligations (e.g., `withdrawDust` shortfall).

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

**File:** rewards/Airdrop.sol (L152-170)
```text
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

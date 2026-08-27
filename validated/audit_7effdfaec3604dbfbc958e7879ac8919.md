### Title
`updateEndRemainingAllocation()` is publicly callable and re-snapshots `totalEndRemainingAllocation`, letting bonus payouts exceed `totalBonus` and draining tokens owed to other claimants - (File: `rewards/Airdrop.sol`)

### Summary
`claim()` only calls `updateEndRemainingAllocation()` automatically when `totalEndRemainingAllocation == 0` [1](#0-0) , but `updateEndRemainingAllocation()` itself is `public`, has no access control, and has no guard against being called more than once [2](#0-1) . Any unprivileged account can call it directly at any time after `periodsEndTime[4]` to overwrite `totalEndRemainingAllocation` with the current (shrinking) `totalRemainingAllocation`, which changes the bonus denominator mid-stream and causes the sum of bonuses paid to exceed `totalBonus`.

### Finding Description
`getBonusAmount()` computes each user's bonus as `userAllocation * totalBonus / totalEndRemainingAllocation` [3](#0-2) . This formula is only mathematically consistent (sum of bonuses == `totalBonus`) if `totalEndRemainingAllocation` is fixed once, at the moment `periodsEndTime[4]` is reached, as a snapshot of the total unclaimed allocation at that point.

The intended one-shot behavior is only enforced at the `claim()` call site via `if (totalEndRemainingAllocation == 0)` [1](#0-0) . But `updateEndRemainingAllocation()` is a standalone `public` function with no `onlyOwner` modifier and no internal guard preventing re-execution — it unconditionally does `totalEndRemainingAllocation = totalRemainingAllocation` whenever `block.timestamp >= periodsEndTime[4]` [4](#0-3) .

Exploit flow:
1. After `periodsEndTime[4]`, the first claimer triggers the automatic snapshot, setting `totalEndRemainingAllocation` to the full remaining allocation total (e.g., 300 across A, B, C).
2. That user claims their bonus computed against denominator 300.
3. An attacker (or anyone) then calls `updateEndRemainingAllocation()` directly — no role required — which overwrites `totalEndRemainingAllocation` to the now-smaller `totalRemainingAllocation` (e.g., 200, after user A's allocation was deducted).
4. Subsequent claimants (B, C) compute their bonus against the smaller denominator (200 instead of 300), inflating their share.
5. The total bonus actually paid out across all claimants now exceeds `totalBonus`, because early and late claimants used different denominators for the same fixed numerator pool.

This over-distribution draws down `aidropToken` balance beyond what accounting (`totalRemainingAllocation` + `totalBonus`) intends, which can starve later legitimate claimants — their `claim()` calls will revert with `InsufficientBalance` [5](#0-4)  once the contract's balance is exhausted, freezing their funds while other claimants (including the attacker if they are also a registered allocatee, or any beneficiary who claims after the re-snapshot) receive more than their entitled share.

### Impact Explanation
This breaks the accounting invariant that sum of all payouts (base allocations + bonus) must reconcile with `aidropToken.balanceOf(address(this))`. Repeated re-snapshotting via the unguarded public `updateEndRemainingAllocation()` allows bonus over-distribution to later claimants at the expense of remaining claimants, who can be left unable to claim their owed tokens (`InsufficientBalance` revert) — a direct loss/freezing of user funds, matching the Critical impact class (direct theft/loss of user funds via broken accounting).

### Likelihood Explanation
No privileged role is required: `updateEndRemainingAllocation()` is `public` with zero access control. The only precondition is `block.timestamp >= periodsEndTime[4]` (natural airdrop lifecycle state) and that at least one claim has already established a nonzero `totalEndRemainingAllocation` while more allocations remain unclaimed (i.e., `totalRemainingAllocation` decreasing over time as users claim, exactly the "most participants have already claimed" scenario described). This is trivially and repeatably triggerable by any EOA at essentially zero cost (a single external call), and can be repeated multiple times to compound the distortion.

### Recommendation
Add a one-shot guard directly inside `updateEndRemainingAllocation()` (e.g., `require(totalEndRemainingAllocation == 0, "already set")` or a boolean `bool public endAllocationSet`) so the snapshot can only ever be taken once regardless of whether it's triggered automatically from `claim()` or called directly by any external account.

### Proof of Concept
Foundry/Hardhat test plan:
1. Deploy `Airdrop` with `startTime` in the near future; fund contract with `aidropToken`.
2. `register()` three users A, B, C each with allocation 100 (`totalRemainingAllocation = 300`).
3. Warp time through periods 0–3, having some other pre-registered users forfeit partial allocations so `totalBonus` accumulates to a known nonzero value (e.g., 60).
4. Warp to `periodsEndTime[4]`.
5. `A.claim()` — this auto-sets `totalEndRemainingAllocation = 300` and pays A `100 + 100*60/300 = 120`.
6. From an unprivileged attacker EOA, call `updateEndRemainingAllocation()` directly — assert it resets `totalEndRemainingAllocation` to `200` (the updated `totalRemainingAllocation`).
7. `B.claim()` and `C.claim()` — each receives `100 + 100*60/200 = 130`.
8. Assert `sum of payouts to A, B, C bonus portions (20+30+30=80) > totalBonus (60)`, demonstrating over-distribution, and/or assert a subsequent legitimate claimant reverts with `InsufficientBalance` due to depleted `aidropToken` balance — confirming the invariant break and fund loss/freezing for later claimants.

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

**File:** rewards/Airdrop.sol (L159-160)
```text
        if(claimableAmount == 0) revert NothingToClaim();
        if(claimableAmount > aidropToken.balanceOf(address(this))) revert InsufficientBalance();
```

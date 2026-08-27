### Title
`updateEndRemainingAllocation()` lacks a one-time-set guard, letting any claimer re-snapshot the bonus denominator downward and steal a disproportionate share of `totalBonus` - (File: `rewards/Airdrop.sol`)

### Summary
`getBonusAmount` divides `totalBonus` by `totalEndRemainingAllocation`, a value that is supposed to be a single, immutable snapshot of `totalRemainingAllocation` taken the first time anyone claims after `periodsEndTime[4]`. However, `updateEndRemainingAllocation()` is `public`, has no access control, and has no internal guard preventing it from being called again — the only guard (`if (totalEndRemainingAllocation == 0)`) exists in `claim()`, not in the function itself. An unprivileged user can therefore wait until most other holders have already claimed (shrinking `totalRemainingAllocation`), call `updateEndRemainingAllocation()` directly to re-snapshot the now much smaller `totalRemainingAllocation`, and then immediately call `claim()` to receive a bonus computed against that shrunk denominator, capturing far more than their proportional share of `totalBonus`.

### Finding Description
The relevant code: [1](#0-0) 

`totalEndRemainingAllocation` is meant to be fixed once, right when `periodsEndTime[4]` is reached, so that every late claimant's bonus share (`userAllocation / totalEndRemainingAllocation * totalBonus`) sums exactly to `totalBonus` across all remaining holders (conservation). But because `updateEndRemainingAllocation()` is public and unguarded, and `totalRemainingAllocation` strictly decreases as each user calls `claim()` (see `totalRemainingAllocation -= userAllocation` in `claim()`), any later caller can re-trigger the snapshot at a point where `totalRemainingAllocation` has already shrunk due to other users' prior claims: [2](#0-1) 

Exploit flow:
1. After `periodsEndTime[4]`, holders start claiming; each claim reduces `totalRemainingAllocation` and, once `claimableAmount >= userAllocation` (always true post period 4), stops adding to `totalBonus`. `totalBonus` is therefore fixed once period 4 begins.
2. Attacker watches the mempool/state for `totalRemainingAllocation` to drop well below the original `totalEndRemainingAllocation` snapshot (i.e., most other loyal holders have already claimed).
3. Attacker calls `updateEndRemainingAllocation()` directly, resetting `totalEndRemainingAllocation` to the now-small `totalRemainingAllocation`.
4. Attacker immediately calls `claim()`. `getBonusAmount` now computes `userAllocation * totalBonus / totalEndRemainingAllocation`, with a denominator artificially shrunk to close to the attacker's own remaining allocation, letting them extract most/all of the remaining `totalBonus` instead of their proportional share.
5. This over-pays the attacker relative to the pool's real backing, breaking the conservation invariant (`sum of all bonus payouts == totalBonus`), and can push the contract into `InsufficientBalance` reverts for subsequent legitimate claimants, freezing their funds.

Existing checks do not stop this: there is no `onlyOwner`/access control on `updateEndRemainingAllocation()`, no re-entrancy issue needed, and no state flag preventing re-snapshotting after the first set (the zero-check guard only lives inside `claim()`, not inside `updateEndRemainingAllocation()` itself).

### Impact Explanation
Direct theft of user funds from the bonus pool: an unprivileged claimant can manipulate the shared `totalEndRemainingAllocation` denominator to capture more than their fair share of `totalBonus`, at the expense of other honest late claimants, and can cause the contract to run out of `aidropToken` balance, permanently freezing legitimate remaining claims (`InsufficientBalance` revert in `claim()`).

### Likelihood Explanation
Low capital and no special privileges are required — only holding an `allocations[_user] > 0` entry and being able to send two ordinary transactions (`updateEndRemainingAllocation()` then `claim()`), timed after other users have already claimed post-`periodsEndTime[4]`. This is trivially repeatable by any claimant and does not require flash loans, reentrancy, or governance/admin rights.

### Recommendation
Add a one-time guard directly inside `updateEndRemainingAllocation()` (e.g., `if (totalEndRemainingAllocation != 0) return;` or a dedicated boolean flag) so the snapshot can only ever be set once, regardless of who or how many times the function is invoked, matching the intent already partially expressed by the `claim()`-side check.

### Proof of Concept
Foundry test plan:
1. Deploy `Airdrop`, `register` several addresses (A, B, attacker) with allocations, warp past `periodsEndTime[4]`.
2. Have A and B call `claim()` first (this sets `totalEndRemainingAllocation` once via the internal check, and reduces `totalRemainingAllocation`).
3. Record `totalBonus` and expected proportional bonus for the attacker based on the original `totalEndRemainingAllocation`.
4. Attacker calls `updateEndRemainingAllocation()` directly (no restriction), then calls `claim()`.
5. Assert attacker's received bonus exceeds `attackerAllocation / originalTotalEndRemainingAllocation * totalBonus` (i.e., more than fair share).
6. Assert `sum(all claimed bonuses) > totalBonus` recorded at period-4 start, violating conservation, and/or that a subsequent legitimate claimant's `claim()` reverts with `InsufficientBalance`.

### Citations

**File:** rewards/Airdrop.sol (L127-150)
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

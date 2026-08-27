### Title
`updateEndRemainingAllocation()` lacks a one-shot guard, letting anyone re-snapshot the bonus denominator to steal disproportionate bonus shares - (File: rewards/Airdrop.sol)

### Summary
`updateEndRemainingAllocation()` is `public`, has no access control and no check that it has already run, so it can be called an unlimited number of times after `periodsEndTime[4]` and will always overwrite `totalEndRemainingAllocation` with whatever `totalRemainingAllocation` currently is. Since `totalRemainingAllocation` shrinks every time a user calls `claim()`, an attacker can wait for honest users to claim (or race the first claim), then call `updateEndRemainingAllocation()` themselves right before their own `claim()` to shrink the bonus denominator and capture an outsized share of `totalBonus`.

### Finding Description
`getBonusAmount()` computes each user's bonus as `userAllocation * totalBonus / totalEndRemainingAllocation`, intending `totalEndRemainingAllocation` to be a single fixed snapshot of `totalRemainingAllocation` taken exactly once when period 4 ends, so that the sum of every user's bonus share is bounded by `totalBonus`: [1](#0-0) 

However `updateEndRemainingAllocation()` re-assigns `totalEndRemainingAllocation = totalRemainingAllocation` on every call with no guard preventing repeated execution, and `claim()` only skips calling it when `totalEndRemainingAllocation == 0` — it does not prevent a separate, direct external call to `updateEndRemainingAllocation()` at any later time: [2](#0-1) 

Exploit flow:
1. After `periodsEndTime[4]`, the first honest `claim()` sets `totalEndRemainingAllocation` for the first time (snapshotting the full remaining pool).
2. As more honest users call `claim()`, `totalRemainingAllocation` decreases (line 164) while `totalBonus` grows from forfeitures (line 167), but `totalEndRemainingAllocation` stays frozen at the old (larger) value — this is the intended, correct state.
3. The attacker (any unprivileged address, with or without an allocation) calls `updateEndRemainingAllocation()` directly. Since it has no access control and no "already set" check, it overwrites `totalEndRemainingAllocation` with the now-smaller `totalRemainingAllocation`.
4. The attacker (or the attacker front-running/waiting for their own turn) then calls `claim()`. Their `getBonusAmount()` is now computed with a shrunk denominator against the still-growing `totalBonus`, yielding a bonus share larger than their true pro-rata entitlement.
5. This breaks the invariant that the sum of all `bonusAmount` payouts must not exceed `totalBonus`; excess value is extracted from the shared airdrop-token balance at the expense of remaining honest claimers, who may later find the contract has `InsufficientBalance` and revert (line 160), i.e., their yield is permanently unclaimable.

No modifier, `nonReentrant`, or state check stops repeated re-snapshotting; the only guard (`totalEndRemainingAllocation == 0`) is bypassed by calling the public function directly instead of going through `claim()`.

### Impact Explanation
This allows an attacker (or any opportunistic actor, including someone who is themselves an allocatee) to capture more than their fair share of `totalBonus`, draining `aidropToken` balance that would otherwise be owed to other participants and potentially leaving later honest claimants unable to claim their allocation/bonus due to `InsufficientBalance`. This is a direct theft of user funds / theft of unclaimed yield matching the "Critical - Direct theft of user funds" impact class.

### Likelihood Explanation
The attack requires no privileged role, no capital beyond gas, and is trivially repeatable: `updateEndRemainingAllocation()` is `public` with zero access control, callable by any EOA or contract at any block after `periodsEndTime[4]`. The precondition (at least one honest claim having reduced `totalRemainingAllocation`, or an attacker racing the very first claim) is naturally and repeatedly satisfied throughout the whole claim window, which lasts until `withdrawDust()` is available (`startTime + 7 * threeMonthsTime`).

### Recommendation
Add a one-shot guard (e.g., a boolean `bool public endRemainingAllocationSet`) so `totalEndRemainingAllocation` can only ever be assigned once, and restrict who/when it can be set — ideally set it automatically and atomically the first time `block.timestamp >= periodsEndTime[4]` is observed (e.g., inside `claim()` guarded by the boolean) rather than exposing a separately callable `public` function:
```solidity
bool public endRemainingAllocationSet;

function updateEndRemainingAllocation() public {
    if (!endRemainingAllocationSet && block.timestamp >= periodsEndTime[4]) {
        totalEndRemainingAllocation = totalRemainingAllocation;
        endRemainingAllocationSet = true;
    }
}
```

### Proof of Concept
Foundry test plan:
1. Deploy `Airdrop` with a `startTime`, fund it with `aidropToken`, and `register()` allocations for users A, B, and Attacker.
2. Warp to `periodsEndTime[4]`.
3. User A calls `claim()` — this triggers the first `updateEndRemainingAllocation()` (since `totalEndRemainingAllocation == 0`), snapshotting `totalEndRemainingAllocation` to the full remaining pool, and A receives allocation + bonus.
4. User B calls `claim()` — `totalRemainingAllocation` decreases further; `totalEndRemainingAllocation` stays unchanged (correct behavior), B receives bonus computed off the original snapshot.
5. Attacker directly calls `updateEndRemainingAllocation()` (bypassing the `claim()` zero-check), which overwrites `totalEndRemainingAllocation` to the now-lower `totalRemainingAllocation`.
6. Attacker calls `claim()`; assert `getBonusAmount(attacker)` is computed against the shrunk `totalEndRemainingAllocation`, and is strictly larger than it would have been under the original snapshot value.
7. Assert `sum(bonusAmount paid to A, B, Attacker) > totalBonus` recorded before step 5 (proving over-payment), and/or that a subsequent honest claimer C reverts with `InsufficientBalance` due to depleted `aidropToken` balance.

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

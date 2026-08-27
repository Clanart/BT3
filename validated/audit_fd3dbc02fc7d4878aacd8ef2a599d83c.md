### Title
`Airdrop.updateEndRemainingAllocation` lacks a set-once guard, letting anyone re-fix the bonus-split snapshot and steal a disproportionate share of `totalBonus` - (File: rewards/Airdrop.sol)

### Summary
`updateEndRemainingAllocation()` overwrites `totalEndRemainingAllocation` with the *current* `totalRemainingAllocation` every time it is called after `periodsEndTime[4]`, with no guard preventing re-execution once a snapshot already exists [1](#0-0) . `claim()` only conditionally triggers it (`if (totalEndRemainingAllocation == 0)`), but that check does not stop anyone from calling the public `updateEndRemainingAllocation()` directly at a self-chosen moment [2](#0-1) . This lets an unprivileged holder of remaining allocation wait until nearly everyone else has claimed (so `totalRemainingAllocation` is very small), call `updateEndRemainingAllocation()` themselves to fix a tiny denominator, then call `claim()` to capture a wildly outsized share of the accumulated `totalBonus` pool computed in `getBonusAmount()` [3](#0-2) .

### Finding Description
`totalBonus` accumulates forfeited allocation from every user who claims before full vesting (period 4) [4](#0-3) . The bonus is meant to be split among the users who *still hold unclaimed allocation* at the moment period 4 starts, proportionally via `userAllocation * totalBonus / totalEndRemainingAllocation`. This snapshot denominator is supposed to be captured exactly once, at the true start of period 4, reflecting the total remaining allocation of all eligible bonus recipients.

The bug is that `updateEndRemainingAllocation()` has no `if (totalEndRemainingAllocation == 0)` guard inside itself — it is a public function that unconditionally reassigns `totalEndRemainingAllocation = totalRemainingAllocation` any time `block.timestamp >= periodsEndTime[4]` [5](#0-4) . The guard in `claim()` (`if (totalEndRemainingAllocation == 0) { updateEndRemainingAllocation(); }`) only prevents `claim()` itself from re-triggering the update, but it does not prevent any other unprivileged caller from invoking `updateEndRemainingAllocation()` directly at any later time to re-fix the snapshot to a new (much smaller) value of `totalRemainingAllocation`.

Exploit flow:
1. Wait until most participants have already claimed pre-period-4 or shortly after, so `totalRemainingAllocation` shrinks toward the attacker's own remaining allocation.
2. Attacker calls `updateEndRemainingAllocation()` directly, resetting `totalEndRemainingAllocation` to this small value.
3. Attacker immediately calls `claim()`; `getBonusAmount()` computes `userAllocation * totalBonus / totalEndRemainingAllocation`, where the denominator has been artificially minimized relative to the attacker's own allocation, letting the attacker extract close to the entire accumulated `totalBonus` pool instead of their fair pro-rata share.

Existing checks do not stop this: `claim()`'s zero-check only guards its own internal call path, not direct external calls to the public `updateEndRemainingAllocation()`; there is no `onlyOnce`/`nonReentrant`/access-control guard on that function at all.

### Impact Explanation
`totalBonus` is capital forfeited by many legitimate users over the life of the airdrop and is meant to be shared proportionally by remaining holders at period-4 start. By repeatedly (and permissionlessly) re-triggering `updateEndRemainingAllocation()` right before their own `claim()`, an attacker can divert a disproportionate — potentially near-total — share of this pooled bonus to themselves, at the direct expense of other legitimate remaining claimants who are entitled to a fair proportional split. This is a direct diversion/theft of other users' entitled airdrop funds, matching the "Direct theft of user funds" impact class.

### Likelihood Explanation
No privileged role is required — `updateEndRemainingAllocation()` and `claim()` are both public/external with no access control [6](#0-5) . The only preconditions are (a) reaching `block.timestamp >= periodsEndTime[4]`, which happens naturally as the airdrop schedule progresses, and (b) `totalRemainingAllocation` being small relative to the attacker's own allocation, which naturally occurs late in the claim period as most participants claim early. This requires no flash loans or special capital — only holding an allocation and timing a transaction — making it realistically exploitable by any attacker paying attention to on-chain state, and repeatable up until the point the value is fixed against the attacker.

### Recommendation
Add an idempotency guard inside `updateEndRemainingAllocation()` itself (not just at the `claim()` call site), e.g.:
```solidity
function updateEndRemainingAllocation() public {
    if (block.timestamp >= periodsEndTime[4] && totalEndRemainingAllocation == 0) {
        totalEndRemainingAllocation = totalRemainingAllocation;
    }
}
```
Additionally consider snapshotting via a dedicated boolean flag (rather than relying on `== 0`, which is ambiguous if `totalRemainingAllocation` is legitimately zero at that time) to unambiguously mark the snapshot as taken exactly once.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `Airdrop` with `startTime` in the near future; `register()` several users with allocations, including `attacker` and `victim`.
2. Warp time to `periodsEndTime[3]` (before period 4) and have most non-attacker users call `claim()`, forfeiting unclaimed portions into `totalBonus`, so `totalRemainingAllocation` shrinks to roughly `attacker`'s + `victim`'s remaining allocation.
3. Warp to `periodsEndTime[4]`.
4. Have `attacker` call `updateEndRemainingAllocation()` directly (before `victim` claims), fixing `totalEndRemainingAllocation` to the current small `totalRemainingAllocation`.
5. Have `attacker` call `claim()` and record the bonus received via `getBonusAmount(attacker)`.
6. Have `victim` call `claim()` afterward and record their bonus.
7. Assert: `attacker`'s bonus share vastly exceeds `attackerAllocation / (attackerAllocation + victimAllocation) * totalBonus`, i.e., disproportionate to their fair pro-rata share, demonstrating value extraction at `victim`'s expense; and demonstrate that calling `updateEndRemainingAllocation()` a second time from a different account after further claims changes `totalEndRemainingAllocation` again, proving the snapshot is not fixed once and is influenceable by transaction ordering.

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

**File:** rewards/Airdrop.sol (L145-157)
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
```

**File:** rewards/Airdrop.sol (L162-168)
```text
        uint256 userAllocation = allocations[msg.sender];
        allocations[msg.sender] = 0;
        totalRemainingAllocation -= userAllocation;
        if (claimableAmount <= userAllocation) {
            uint256 forfeited = userAllocation - claimableAmount;
            totalBonus += forfeited;
        }
```

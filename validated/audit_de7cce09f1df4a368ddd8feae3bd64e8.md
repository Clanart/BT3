### Title
Repeated calls to `Airdrop.updateEndRemainingAllocation()` let a late claimant shrink the bonus denominator and extract more than its proportional share of `totalBonus`, over-paying bonuses beyond the forfeited pool and depleting principal reserved for other claimants - (File: rewards/Airdrop.sol)

### Summary
`totalEndRemainingAllocation` is intended to be a one-time snapshot of unclaimed allocations taken at `periodsEndTime[4]`, used as the denominator for proportional bonus distribution in `getBonusAmount`. However `updateEndRemainingAllocation()` is `public`, has no "already set" guard, and can be invoked by anyone repeatedly after `periodsEndTime[4]`, re-syncing the denominator to the ever-shrinking `totalRemainingAllocation` as other users claim. An unprivileged, self-interested late claimer can call this function immediately before its own `claim()` to shrink the denominator relative to the (unchanged) `totalBonus` numerator, extracting a bonus larger than its true proportional share and causing the sum of all bonus payouts to exceed `totalBonus`.

### Finding Description
`getBonusAmount` computes a claimant's bonus as `userAllocation * totalBonus / totalEndRemainingAllocation` [1](#0-0) . `totalEndRemainingAllocation` is meant to be fixed once at period end, but `updateEndRemainingAllocation()` unconditionally overwrites it to the current `totalRemainingAllocation` every time it's called (with no state guard beyond the time check), and it is a `public` function callable by anyone: [2](#0-1) .

`claim()` only auto-invokes this helper when `totalEndRemainingAllocation == 0`, meaning after the first call sets it non-zero, `claim()` itself will not re-trigger it — but nothing stops an attacker from calling the public `updateEndRemainingAllocation()` directly at any later time: [3](#0-2) .

Since `totalRemainingAllocation` is decremented by every `claim()` call (both early forfeitures and full late claims) [4](#0-3) , an attacker who is a late claimant can:
1. Wait for other late claimants to claim first, shrinking `totalRemainingAllocation`.
2. Call `updateEndRemainingAllocation()` right before its own `claim()`, resyncing `totalEndRemainingAllocation` down to the now-smaller `totalRemainingAllocation`.
3. Call `claim()`, computing its bonus against the same fixed `totalBonus` but a much smaller denominator, receiving a bonus larger than its fair share of the original snapshot.

Because `totalBonus` is never decremented as bonuses are paid, repeating this pattern across successive late claimants causes cumulative bonus payouts to exceed `totalBonus`, drawing down the token balance that should back other users' base (non-bonus) allocations. This directly conflicts with the conservation invariant implied by the code's own comment ("This will store the ending remaining amount for the bonus"), which assumes a single, immutable snapshot. The only backstop, `if(claimableAmount > aidropToken.balanceOf(address(this))) revert InsufficientBalance()` [5](#0-4) , does not prevent the over-payment to earlier attackers — it merely reverts and locks out later legitimate claimants once the pool is drained, converting the theft into value loss for other honest users (either direct theft to the manipulating claimant or permanent freezing of the remaining honest claimants' entitled tokens).

### Impact Explanation
This is a direct theft of the airdrop token pool: an unprivileged, allocation-holding wallet can extract bonus tokens beyond the amount fairly attributable to its share of `totalEndRemainingAllocation`, at the expense of other legitimate claimants whose principal/bonus allocation becomes under-funded or entirely unclaimable once the contract's balance is exhausted. This matches the "direct theft of user funds" / "permanent freezing of funds (≥24h)" Immunefi impact classes, since remaining users could be permanently blocked from claiming their rightful allocation once the pool is drained by earlier manipulators.

### Likelihood Explanation
- Requires only holding a nonzero `allocations[attacker]` (achieved simply by being a registered eligible airdrop recipient) and waiting until after `periodsEndTime[4]`.
- `updateEndRemainingAllocation()` is a public function with no access control and no cooldown, callable at zero cost beyond gas.
- No flash loan or reentrancy needed; the attacker only needs to sequence two of its own transactions (or front-run/back-run other claimants) around the period-end window, which is trivially repeatable by any of the last late-claiming users, especially the very last claimant who could set the denominator down to just its own allocation and effectively drain all remaining `totalBonus` for itself.

### Recommendation
Make the snapshot immutable and self-triggering only once:
- Add a guard so `updateEndRemainingAllocation()` can only set `totalEndRemainingAllocation` the first time it becomes non-zero (e.g., `if (totalEndRemainingAllocation == 0 && block.timestamp >= periodsEndTime[4]) { totalEndRemainingAllocation = totalRemainingAllocation; }`), matching the guard already used inside `claim()`.
- Alternatively, remove the public function entirely and only snapshot internally the first time `claim()` is called after `periodsEndTime[4]`, storing it in a way that cannot be reset.

### Proof of Concept
Hardhat test plan:
1. Deploy `Airdrop` with `startTime = now + 1`; register 4 users A, B, C, D with equal allocations `X` each.
2. Warp past `startTime`. Have A and B call `claim()` before `periodsEndTime[1]` (partial period), each forfeiting `~90%*X` into `totalBonus` (`totalBonus ≈ 1.8X`).
3. Warp past `periodsEndTime[4]`. At this point `totalRemainingAllocation = 2X` (C and D still unclaimed).
4. C calls `claim()` first (triggers `updateEndRemainingAllocation()` since it's still 0, snapshotting `totalEndRemainingAllocation = 2X`). Assert C receives `bonus_C = X * totalBonus / 2X = 0.9X`.
5. `totalRemainingAllocation` is now `X` (only D left). Before D claims, D (attacker) directly calls `updateEndRemainingAllocation()`, resetting `totalEndRemainingAllocation = X`.
6. D calls `claim()`. Assert D receives `bonus_D = X * totalBonus / X = totalBonus (1.8X)`, i.e., D alone extracts the *entire* `totalBonus` pool even though C already consumed `0.9X` of it — total bonus paid out (`0.9X + 1.8X = 2.7X`) exceeds `totalBonus` (`1.8X`), proving the conservation violation and confirming the excess is drawn from principal reserved for other users, potentially triggering `InsufficientBalance()` for any remaining legitimate claim. [6](#0-5)

### Citations

**File:** rewards/Airdrop.sol (L106-170)
```text
    function getClaimableAmount(address _user)
        public
        view
        returns (uint256 claimableAmount)
    {
        uint256 userAllocation = allocations[_user];
        claimableAmount = 0;
        if (userAllocation > 0) {
            for (uint256 i = 0; i < 5; i++) {
                if (block.timestamp >= periodsEndTime[i]) {
                    claimableAmount += userAllocation * percentPerPeriod[i];
                }
            }
            claimableAmount /= denominator;
            claimableAmount += getBonusAmount(_user);
        }
    }

    /// @notice Get the bonus amount for user
    /// @param _user The user to get the bonus amount for
    /// @return bonusAmount The vonus amount
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

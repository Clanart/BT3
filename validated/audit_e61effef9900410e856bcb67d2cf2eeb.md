### Title
Repeatable `updateEndRemainingAllocation()` allows attacker to shrink the bonus divisor before claiming, stealing bonus tokens from honest holders - (File: `rewards/Airdrop.sol`)

### Summary
`updateEndRemainingAllocation()` is public, unguarded, and can be called an unlimited number of times after `periodsEndTime[4]`, each time re-snapshotting `totalEndRemainingAllocation` to the *current* (shrinking) `totalRemainingAllocation`. Because `getBonusAmount()` divides the fixed `totalBonus` pool by whatever `totalEndRemainingAllocation` happens to be at claim time, an attacker who waits for other holders to claim (removing their allocation from the denominator) and then re-triggers the snapshot before claiming can capture a wildly disproportionate share of `totalBonus`, breaking conservation and directly harming holders who claim earlier or afterward with insufficient contract balance.

### Finding Description
The relevant state and functions: [1](#0-0) 

`updateEndRemainingAllocation()` has **no guard** preventing it from being called again after it has already been set once. `claim()` only auto-calls it `if (totalEndRemainingAllocation == 0)`, but since the function is `public` and unrestricted, anyone can call it directly at any later time, repeatedly overwriting `totalEndRemainingAllocation` with the then-current `totalRemainingAllocation`.

Because each `claim()` removes the claimer's full `userAllocation` from `totalRemainingAllocation` (`registration/rewards/Airdrop.sol:164`), the value monotonically shrinks as holders claim. An attacker (needing no special privilege - just being a registered holder, or controlling several registered addresses) can:
1. Wait for the first legitimate holder(s) to claim after `periodsEndTime[4]` (this shrinks `totalRemainingAllocation`).
2. Call `updateEndRemainingAllocation()` themselves to re-snapshot `totalEndRemainingAllocation` to this smaller value.
3. Immediately claim - `getBonusAmount()` now divides the unchanged `totalBonus` numerator by a much smaller denominator, inflating their bonus.
4. Repeat between each of their own claims (using multiple registered addresses) to progressively shrink the denominator, maximizing the payout of the last wallet to claim.

This breaks the conservation invariant that `Σ bonusAmount(claimers) == totalBonus`. Since the numerator (`totalBonus`) never changes after `periodsEndTime[4]` (no more forfeiture occurs for fully-vested claims) while the denominator can be repeatedly shrunk, total bonus paid out can vastly exceed `totalBonus`, at the direct expense of tokens meant for the remaining/honest holders (who either get less than their fair share if they claim after the pool is drained, or hit `InsufficientBalance()` and have their claim reverted/frozen).

Existing checks that fail to stop this: there is no `nonReentrant`, no access control, and no "set-once" flag on `updateEndRemainingAllocation()`; `claim()`'s balance check (`claimableAmount > aidropToken.balanceOf(address(this))`) only protects against reverting the current transaction, it does not prevent the underlying pool-share manipulation and can itself cause legitimate late claimants to be permanently unable to claim their fair share if the balance is drained by early manipulators.

### Impact Explanation
Direct theft of bonus tokens meant for honest holders and/or protocol insolvency: an attacker manipulating claim order and repeatedly re-triggering the snapshot can capture far more than their pro-rata share of `totalBonus`, draining the airdrop token balance so that honest holders who claim later cannot receive their fair (or any) bonus/allocation - qualifying as both "direct theft of user funds" and "permanent freezing of funds" for the remaining legitimate claimants.

### Likelihood Explanation
- No special privilege needed: `updateEndRemainingAllocation()` is `public` and callable by any address.
- Only precondition is being a registered holder (or controlling multiple registered addresses, as stated in the prompt) and waiting until `block.timestamp >= periodsEndTime[4]`.
- Fully repeatable and deterministic — the attacker fully controls the order and timing of their own claims and the interleaved `updateEndRemainingAllocation()` calls.
- Requires no flash loan or reentrancy; simple sequencing of ordinary transactions is sufficient.

### Recommendation
Make the `totalEndRemainingAllocation` snapshot immutable once set: add a guard (e.g., `if (totalEndRemainingAllocation != 0) return;` or a boolean `endAllocationSet` flag) inside `updateEndRemainingAllocation()` so it can only ever be written once, at the first call after `periodsEndTime[4]`. This restores the invariant that every claimant's bonus share is computed against the same fixed denominator captured at the true end of the vesting period.

### Proof of Concept
Foundry test plan (`Airdrop.t.sol`):
1. Deploy `Airdrop` with `startTime = block.timestamp + 1`, fund the contract with airdrop tokens.
2. `register()` 5 addresses: `H` (honest, alloc=100), `A1..A4` (attacker-controlled, alloc=100 each). Total registered = 500.
3. Warp to some time before `periodsEndTime[4]`, have an unrelated seed claim (or one of the attacker wallets claiming early) forfeit tokens to build `totalBonus = 90` (e.g., via a 6th early-claiming address that forfeits 90 tokens), without affecting `H`/`A1..A4` allocations.
4. Warp to `block.timestamp >= periodsEndTime[4]`.
5. `H.claim()` — this auto-triggers `updateEndRemainingAllocation()`, snapshotting `totalEndRemainingAllocation = 500`; assert `bonusAmount(H) == 100*90/500 == 18`.
6. Attacker calls `updateEndRemainingAllocation()` manually (re-snapshot to `400`), then `A1.claim()`; assert `bonusAmount(A1) == 100*90/400 == 22.5` (inflated vs. fair 18).
7. Repeat step 6 for `A2` (denominator 300 → bonus 30), `A3` (denominator 200 → bonus 45), `A4` (denominator 100 → bonus 90).
8. Assert `Σ bonusAmount(H, A1..A4) == 18+22.5+30+45+90 = 205.5 > totalBonus (90)`, proving conservation is broken and that later claimers (`A4` especially) receive a disproportionate, unfair share versus the true pro-rata baseline (`100/500*90=18` for every equally-sized holder) that would result if `totalEndRemainingAllocation` were locked once at `periodsEndTime[4]`.
9. Optionally show that with a constrained token balance, `H`'s later attempt to claim (if reordered) reverts with `InsufficientBalance()`, demonstrating fund freezing for the honest holder. [2](#0-1) [3](#0-2)

### Citations

**File:** rewards/Airdrop.sol (L127-170)
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

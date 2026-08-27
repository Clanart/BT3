### Title
Unbounded re-snapshotting of `totalEndRemainingAllocation` in `updateEndRemainingAllocation()` allows an attacker to over-extract `totalBonus`, stealing tokens owed to other remaining-allocation holders - (File: rewards/Airdrop.sol)

### Summary
`updateEndRemainingAllocation()` is a public, unguarded function that can be called repeatedly after `periodsEndTime[4]`, each time re-setting the bonus-share denominator `totalEndRemainingAllocation` to the *current* `totalRemainingAllocation`. Because `totalRemainingAllocation` strictly shrinks as each holder claims, an attacker can inflate `totalBonus` via a cheap self-forfeiture and then re-trigger the snapshot immediately before their own late claim, after other legitimate holders have already claimed against an earlier (larger) denominator, causing the sum of all bonus payouts to exceed the true `totalBonus` pool.

### Finding Description
`claim()` [1](#0-0)  forfeits any unclaimed portion of a caller's allocation into `totalBonus` whenever `claimableAmount <= userAllocation` (i.e., pre-`periodsEndTime[4]` claims), and always subtracts the *full* original `userAllocation` from `totalRemainingAllocation`, regardless of when the claim happens.

`getBonusAmount()` [2](#0-1)  distributes `totalBonus` to post-period-4 claimants proportional to `userAllocation / totalEndRemainingAllocation`. This denominator is meant to be a fixed snapshot of "who still hadn't claimed when period 4 began."

However, `updateEndRemainingAllocation()` [3](#0-2)  is `public` with no access control and no "only-once" guard - it simply re-assigns `totalEndRemainingAllocation = totalRemainingAllocation` any time `block.timestamp >= periodsEndTime[4]`. The internal call inside `claim()` only prevents redundant self-triggering when `totalEndRemainingAllocation == 0` [4](#0-3) , but anyone can call the public function directly at any later time to force a fresh, smaller snapshot, since `totalRemainingAllocation` keeps decreasing every time someone claims (line 164).

Exploit flow:
1. Attacker's address A (large allocation `a`) calls `claim()` well before `periodsEndTime[4]`, forfeiting most of `a` into `totalBonus` at near-zero cost (they still receive their small vested share, they just accelerate forfeiture of the rest).
2. Once `periodsEndTime[4]` arrives, legitimate holders (`R`) claim normally; the first such claim (or an explicit call) fixes `totalEndRemainingAllocation` at a value that fairly includes `R` and B's allocation `b` (e.g. `b + R`). These legitimate holders receive their fair proportional share of `totalBonus`.
3. As `R` holders claim, `totalRemainingAllocation` shrinks toward `b` alone (since claimed allocations are fully removed).
4. Attacker calls `updateEndRemainingAllocation()` again (no guard prevents this), re-snapshotting `totalEndRemainingAllocation` down to `b` (or close to it).
5. Attacker's address B then calls `claim()`, computing `bonusAmount = b * totalBonus / b = totalBonus` - i.e., B captures the **entire** `totalBonus` a second time, even though the `R` holders were already paid their share from the same pool in step 2.

This breaks the conservation invariant: the sum of bonus payouts across all claimants can exceed `totalBonus`, meaning tokens are paid out that were never forfeited by anyone - the excess is extracted from the contract's balance that is otherwise earmarked for other users' vested principal, exposing later legitimate claimants to `InsufficientBalance` reverts (line 160), i.e., funds frozen/unclaimable for those users.

### Impact Explanation
Direct theft of tokens from the shared bonus pool / general remaining-allocation reserve at the expense of other, non-colluding registered users, and potential permanent freezing of legitimate users' vested allocation when the contract balance is depleted below what they are owed (`InsufficientBalance` revert in `claim()`). This matches an Immunefi "theft of user funds" / "permanent freezing of funds" impact class.

### Likelihood Explanation
Requires only two attacker-controlled registered addresses (one sacrificial with a sizeable allocation) and the presence of at least one other legitimate registrant who claims post-`periodsEndTime[4]` before the attacker's final re-snapshot call. No privileged role, oracle, or governance action is needed - `claim()` and `updateEndRemainingAllocation()` are both permissionless `external`/`public` functions. The attack is fully repeatable for every airdrop deployment using this contract and only costs gas plus the (recoverable) sacrificial forfeiture.

### Recommendation
Make `totalEndRemainingAllocation` an immutable-once-set snapshot: guard `updateEndRemainingAllocation()` so it can only execute successfully one time (e.g., `if (totalEndRemainingAllocation != 0 || totalRemainingAllocation == 0) return;` or add an explicit `bool endSnapshotTaken` flag), ensuring every claimant's bonus is computed against the same fixed denominator captured at the true start of the post-period-4 window.

### Proof of Concept
Foundry test plan:
1. Deploy `Airdrop` with `startTime = block.timestamp + 1`.
2. `register()` three addresses: attacker A (`allocA = 100_000e18`), attacker B (`allocB = 1_000e18`), legit user L (`allocL = 10_000e18`). Fund contract with `allocA + allocB + allocL`.
3. Warp to just after `periodsEndTime[0]`; A calls `claim()` - assert `totalBonus` increases by `~0.9 * allocA`, `totalRemainingAllocation` decreases by full `allocA`.
4. Warp to `periodsEndTime[4]`.
5. L calls `claim()` first - internally triggers `updateEndRemainingAllocation()` snapshot at `allocB + allocL`; assert L receives `allocL + allocL/(allocB+allocL) * totalBonus`.
6. Attacker calls `updateEndRemainingAllocation()` again directly - assert `totalEndRemainingAllocation` is now reduced to `allocB` only.
7. B calls `claim()` - assert B receives `allocB + totalBonus` (full bonus again).
8. Assert: `(claim of A) + (claim of B) + (claim of L) > allocA + allocB + allocL` (i.e., total tokens paid out exceed total tokens ever allocated/forfeited), proving conservation is broken and that L's fair share was effectively double-paid to the attacker. Optionally show a fourth legitimate user reverting with `InsufficientBalance` when attempting to claim afterward due to depleted contract balance.

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

**File:** rewards/Airdrop.sol (L153-170)
```text
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

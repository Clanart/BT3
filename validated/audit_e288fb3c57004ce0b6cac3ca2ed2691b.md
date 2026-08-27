## Finding

The `FUEL-Contracts` report concerns a bookkeeping variable (`totalAllocatedTokens`) that could fall out of sync with what is actually distributable, allowing tokens meant for later distribution to be mishandled. The analogous flaw in this codebase is in `rewards/Airdrop.sol`, where the per-user allocation state is fully zeroed on the *first* `claim()` call, even though the airdrop is designed to vest in five time-gated tranches. This causes permanent loss of a user's un-vested remaining allocation.

### Title
Premature zeroing of `allocations` in `Airdrop.claim()` permanently forfeits users' unvested tranches - (File: `rewards/Airdrop.sol`)

### Summary
`Airdrop.claim()` computes the cumulative vested amount to date via `getClaimableAmount`, transfers it, and then unconditionally sets `allocations[msg.sender] = 0`, deleting the user's entire allocation record instead of only the portion already claimed.

### Finding Description
The airdrop is structured into 5 periods with increasing cumulative unlock percentages (`percentPerPeriod`: 10%, 10%, 20%, 30%, 30%), intended to let users progressively claim more of their allocation over ~15 months [1](#0-0) .

`getClaimableAmount` looks up `allocations[_user]` and multiplies it by the sum of percentages for all periods that have elapsed so far [2](#0-1) . This design assumes `allocations[_user]` remains the user's *original* total allocation across the whole vesting schedule, similar to how `MGPRelease.sol` tracks `vesting.totalAlloced` immutably and separately tracks `vesting.claimed` [3](#0-2) [4](#0-3) .

However, unlike `MGPRelease.sol`, `Airdrop.sol` has no separate `claimed` field. Instead, `claim()` transfers the currently-vested `claimableAmount` and then sets the *entire* `allocations[msg.sender]` to zero: [5](#0-4) 

Since `getClaimableAmount` depends entirely on `allocations[_user]` being non-zero, any user who calls `claim()` before the final period (`periodsEndTime[4]`) has elapsed will receive only the currently-vested fraction (e.g., 10% if claiming during period 0), and then permanently loses access to the remaining un-vested allocation, because `allocations[msg.sender]` is now `0` and will remain `0` for all future calls to `getClaimableAmount`/`claim()`.

### Impact Explanation
This results in permanent freezing/loss of user funds: the un-vested remainder of a user's allocation becomes unclaimable by that user for the rest of the airdrop's lifetime, since the accounting variable that gates eligibility (`allocations`) is destroyed rather than decremented by the claimed portion. Because `totalRemainingAllocation` is also decremented by the full `userAllocation` at the same time [6](#0-5) , those forfeited tokens are not redistributed to other users as "bonus" (that only happens when `claimableAmount <= userAllocation` post period 4) — they simply remain in the contract and are eventually swept out entirely to the owner via `withdrawDust()` [7](#0-6) . This is a direct, unprivileged-wallet-triggered permanent loss of the user's own unclaimed vested tokens, exceeding the 24-hour freeze threshold (the vesting schedule spans ~15 months).

### Likelihood Explanation
Any ordinary allocated user who calls `claim()` early — which is the intuitive, expected action for a participant checking on and claiming their airdrop as soon as it becomes claimable — triggers this bug. There is no privileged role or special condition required; it is a natural, likely user interaction given the contract explicitly supports claiming across multiple periods.

### Recommendation
Track claimed amounts separately (as `MGPRelease.sol` does with `vesting.claimed`) rather than zeroing the user's full allocation on the first claim. `getClaimableAmount` should compute `cumulativeVested(_user) - claimed[_user]`, and `claim()` should only decrement `totalRemainingAllocation` by the amount actually transferred, incrementing `claimed[_user]` by the same amount instead of deleting `allocations[msg.sender]`.

### Proof of Concept
1. Owner calls `register([alice], [1000e18])` before `startTime`.
2. At `block.timestamp == periodsEndTime[0]` (period 0, 10% unlocked), Alice calls `claim()`.
   - `getClaimableAmount` returns `1000e18 * 1000 / 10000 = 100e18`.
   - `aidropToken.safeTransfer(alice, 100e18)` executes.
   - `allocations[alice]` is set to `0`.
3. At `block.timestamp >= periodsEndTime[4]` (100% should be unlocked), Alice calls `claim()` again.
   - `getClaimableAmount(alice)` reads `allocations[alice] == 0`, so `claimableAmount = 0`.
   - `claim()` reverts with `NothingToClaim()`.
4. Alice has permanently forfeited the remaining `900e18` tokens she was entitled to, which sit in the contract until `withdrawDust()` sends them to the owner.

### Citations

**File:** rewards/Airdrop.sol (L45-54)
```text
        periodsEndTime[0] = startTime;
        periodsEndTime[1] = startTime + threeMonthsTime;
        periodsEndTime[2] = startTime + 2 * threeMonthsTime;
        periodsEndTime[3] = startTime + 3 * threeMonthsTime;
        periodsEndTime[4] = startTime + 4 * threeMonthsTime;
        percentPerPeriod[0] = 1000;
        percentPerPeriod[1] = 1000;
        percentPerPeriod[2] = 2000;
        percentPerPeriod[3] = 3000;
        percentPerPeriod[4] = 3000;
```

**File:** rewards/Airdrop.sol (L94-98)
```text
    function withdrawDust() external onlyOwner {
        if(block.timestamp < startTime + 7 * threeMonthsTime) revert AirdropNotEnded();

        aidropToken.safeTransfer(owner(), aidropToken.balanceOf(address(this)));
    }
```

**File:** rewards/Airdrop.sol (L106-122)
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
```

**File:** rewards/Airdrop.sol (L153-169)
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
```

**File:** rewards/MGPRelease.sol (L18-22)
```text
    struct Vesting {
        uint256 totalAlloced;
        uint256 claimed;
        bool revoked;
    } 
```

**File:** rewards/MGPRelease.sol (L80-94)
```text
    function getClaimable(address _account) public view returns (uint256 claimable) {
        Vesting storage vesting = beneficiaries[_account];
        uint256 initialUnlockedAmount = vesting.totalAlloced * initialUnlockPercentage / denominator;

        if (block.timestamp <= startTimestamp)
            return  initialUnlockedAmount - vesting.claimed;

        if (block.timestamp >= endTimestamp)
            return vesting.totalAlloced - vesting.claimed;

        uint256 needVesting = vesting.totalAlloced - initialUnlockedAmount;
        uint256 vested = (((block.timestamp - startTimestamp) * needVesting) / (endTimestamp - startTimestamp));

        claimable = (initialUnlockedAmount + vested - vesting.claimed);
    }    
```

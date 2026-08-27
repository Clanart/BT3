## Analysis

The reported bug class is: a normal, expected owner action ("sweep remaining/dust funds") does not account for funds still owed to users who have a *legitimate, unprivileged, time-unrestricted claim right*, resulting in the contract insolvent for those later claims — not a malicious-admin scenario, but a systemic accounting gap.

This exact pattern exists in `MGPRelease.sol`, the MGP vesting contract.

### Root cause
`claim()` is a fully permissionless, unprivileged-wallet function with **no deadline** — it remains callable indefinitely after `endTimestamp`, always paying out `vesting.totalAlloced - vesting.claimed`: [1](#0-0) 

`withdrawDust()` is intended as a cleanup mechanism for genuine leftover dust after vesting completes, gated only by a fixed buffer (`timeInSecBeforeWithdrawDust`) past `endTimestamp`. It does **not** compute or reserve the sum of all unclaimed beneficiary entitlements (`Σ(totalAlloced - claimed)` across all registered beneficiaries) — it simply sweeps the **entire** token balance of the contract to the owner: [2](#0-1) 

Because the buffer is a static, protocol-wide delay rather than a per-beneficiary "has this user claimed" check, any beneficiary who simply hasn't called `claim()` yet by the time the buffer elapses (e.g., low gas urgency, wallet inactivity, waiting for a better time) will have their still-unclaimed, already-vested MGP swept away in a routine, non-malicious owner call. Their subsequent `claim()` call will revert due to insufficient contract balance, permanently freezing their already-earned tokens — mirroring the original report's core defect (mint-after-end vs. `withdrawRemainingTokens` not accounting for pending receipts).

The same structural gap (time-boxed, no-per-user-check "withdraw remaining" sweep versus an always-claimable user function) is also present in `Airdrop.sol`'s `withdrawDust()`/`getClaimableAmount()` pair: [3](#0-2) 

### Title
Vesting `claim()` has no deadline while `withdrawDust()` sweeps the full balance without reserving unclaimed vested amounts, permanently freezing user funds - (File: rewards/MGPRelease.sol)

### Summary
`MGPRelease.claim()` lets a beneficiary claim their fully-vested MGP allocation at any time after `endTimestamp`, with no expiry. `withdrawDust()`, callable by the owner once `timeInSecBeforeWithdrawDust` has elapsed after `endTimestamp`, transfers the contract's **entire** token balance to `owner()` instead of only the true leftover dust (i.e., balance minus the sum of all beneficiaries' still-unclaimed vested amounts). A beneficiary who has not yet called `claim()` by that time loses access to their already-vested tokens permanently.

### Finding Description
`getClaimable()` computes a beneficiary's claimable amount purely as a function of elapsed time versus `startTimestamp`/`endTimestamp`, with `claim()` never checking whether the vesting period is still "open" for withdrawal purposes — it is meant to remain claimable forever until fully drawn down. [1](#0-0) 

`withdrawDust()`, however, does not track or query outstanding per-beneficiary balances; it uses `IERC20(tokenToRelease).balanceOf(address(this))` as the amount to withdraw, which necessarily includes any tokens still owed to beneficiaries who registered but have not yet called `claim()`. [2](#0-1) 

This is the same root-cause shape as the QuestFactory finding: an unprivileged, deadline-less user action (`mintReceipt`/`claim`) is not reconciled against a normal admin sweep (`withdrawRemainingTokens`/`withdrawDust`) that only accounts for what has already been recorded/claimed rather than everything still owed.

### Impact Explanation
Any beneficiary who has not called `claim()` before the owner calls `withdrawDust()` (a completely normal, intended contract operation, not requiring malicious intent) will find the contract's token balance drained below what is required to pay them. Their subsequent `claim()` call reverts (insufficient balance for `safeTransfer`), permanently freezing their already-vested MGP allocation — satisfying the "permanent freezing of funds" impact bar.

### Likelihood Explanation
This requires no attacker and no malicious admin decision — it can occur purely from ordinary operational timing: the owner calling a documented, permissionless-by-design cleanup function after the configured buffer, while one or more legitimate beneficiaries simply haven't gotten around to claiming (identical to the judge's accepted reasoning in the original finding that "determining if everyone has minted/claimed yet is not straightforward"). Given `timeInSecBeforeWithdrawDust` is a fixed, project-chosen buffer and there is no on-chain signal of "did all beneficiaries claim," this is a realistic, foreseeable occurrence rather than a contrived edge case.

### Recommendation
Track a running total of allocated-but-unclaimed vesting (e.g., increment on `register`, decrement on `claim`) and have `withdrawDust()` only transfer `balanceOf(this) - totalUnclaimedAllocated`, ensuring outstanding beneficiary claims always remain fully covered regardless of when `withdrawDust()` is invoked. The same fix should be applied to `Airdrop.sol`'s `withdrawDust()`.

### Proof of Concept
1. Owner deploys `MGPRelease` with `startTimestamp`, `endTimestamp`, and a `timeInSecBeforeWithdrawDust` buffer, then funds it and calls `register([alice], [1000e18])`.
2. Time passes to `endTimestamp` — Alice is now entitled to claim her full `1000e18` but has not yet called `claim()` (e.g., she is waiting to batch her gas spend).
3. Time passes `timeInSecBeforeWithdrawDust` further; owner calls `withdrawDust()`, which transfers the contract's entire remaining balance (including Alice's `1000e18`) to `owner()`.
4. Alice calls `claim()` — `getClaimable(alice)` still returns `1000e18`, but `safeTransfer` reverts because the contract no longer holds sufficient tokens, permanently freezing her vested allocation.

### Citations

**File:** rewards/MGPRelease.sol (L80-108)
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

    /* ============ External Functions ============ */

    function claim() nonReentrant external {
        Vesting storage vesting = beneficiaries[msg.sender];
        if (vesting.revoked)
            revert AccountRevoked();
        
        uint256 claimable = getClaimable(msg.sender);
        IERC20(tokenToRelease).safeTransfer(msg.sender, claimable);
        vesting.claimed += claimable;

        emit Claimed(msg.sender, claimable);
    }
```

**File:** rewards/MGPRelease.sol (L142-150)
```text
    function withdrawDust() external onlyOwner() {
        if (block.timestamp < endTimestamp + timeInSecBeforeWithdrawDust)
            revert WithdrawDustNotAllowedYet();

        uint256 dustAmount = IERC20(tokenToRelease).balanceOf(address(this));
        IERC20(tokenToRelease).safeTransfer(owner(), dustAmount);

        emit DustWithdraw(owner(), dustAmount);
    }
```

**File:** rewards/Airdrop.sol (L93-122)
```text
    /// @notice 21 months after start date, 9 months after end date, owner will be able to collect the remaining allocations considering the users will not.
    function withdrawDust() external onlyOwner {
        if(block.timestamp < startTime + 7 * threeMonthsTime) revert AirdropNotEnded();

        aidropToken.safeTransfer(owner(), aidropToken.balanceOf(address(this)));
    }

    /* ============ External Functions ============ */

    /// @notice Get the claimable MGP amount of _user
    /// @param _user The user to get the claimable amount for
    /// @dev includes the bonus amount
    /// @return claimableAmount The claimable amount
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

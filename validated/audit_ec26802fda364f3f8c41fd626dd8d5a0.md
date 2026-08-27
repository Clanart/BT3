### Title
Permanent Loss of Already-Vested but Unclaimed MGP Tokens on Revocation - (File: `rewards/MGPRelease.sol`)

### Summary
`MGPRelease.revoke()` sets a beneficiary's `revoked` flag to `true` without first settling the tokens that have already vested up to that point in time. Once `revoked` is `true`, `claim()` unconditionally reverts with `AccountRevoked()`, permanently blocking the beneficiary from ever withdrawing the portion of tokens that had already vested (and to which they are contractually entitled) before the revocation occurred. This mirrors the RAAC `emergencyRevoke` bug class: a revocation mechanism that fails to distinguish between vested (rightfully owed) and unvested tokens, causing loss of funds that should be guaranteed to the beneficiary.

### Finding Description
The vesting logic computes claimable amounts on a linear schedule in `getClaimable`: [1](#0-0) 

`claim()` transfers whatever is currently vested to the caller, but is fully gated by the `revoked` flag with no path to ever settle vested-but-unclaimed tokens once that flag is set: [2](#0-1) 

The revocation function itself performs no calculation of the vested amount, no transfer to the beneficiary, and no partial-settlement logic — it simply flips a boolean: [3](#0-2) 

Because `claim()` checks `vesting.revoked` before computing/paying out `getClaimable`, a revocation instantaneously and irrevocably forfeits **all** of the beneficiary's tokens — including the fraction that had already linearly vested and would otherwise have been immediately withdrawable — not just the still-unvested remainder. There is no accounting difference in the `Vesting` struct between "vested-not-yet-claimed" and "not-yet-vested" amounts, so the flaw is structurally identical to the reported `RAACReleaseOrchestrator.emergencyRevoke` root cause: absence of a "pay out vested, then claw back only unvested" step before disabling further claims.

### Impact Explanation
Any beneficiary registered via `register()` who has partially vested tokens at the time `revoke(beneficiary, true)` is called permanently loses those already-earned, unclaimed tokens — they become stuck in the contract forever (the only way out, `withdrawDust`, only sends the balance to the owner after `endTimestamp + timeInSecBeforeWithdrawDust`, and does not restore any per-beneficiary accounting). This is a permanent freezing/loss of legitimately vested (i.e., earned) yield/allocation belonging to an ordinary, unprivileged beneficiary wallet, matching the "theft or permanent freezing of unclaimed yield" impact bar.

### Likelihood Explanation
`revoke` is a normal, documented admin lifecycle operation for this vesting contract (analogous to `emergencyRevoke` in the reference report — not a malicious extra-privilege abuse, but the contract's intended revocation feature working exactly as coded). Any legitimate revocation of a beneficiary who has already begun vesting (which is the common case, since `startTimestamp` passes early in the vesting lifecycle) will trigger this loss deterministically — no attacker coordination, race condition, or edge-case timing is required.

### Recommendation
Before setting `revoked = true`, compute `getClaimable(_account)` and transfer that amount to the beneficiary (or otherwise credit it as claimed/exempt from revocation), so only the truly unvested remainder is affected by the revocation:

```solidity
function revoke(address _account, bool _isRevoked) external onlyOwner() {
    Vesting storage vesting = beneficiaries[_account];
    if (_isRevoked && !vesting.revoked) {
        uint256 claimable = getClaimable(_account);
        if (claimable > 0) {
            vesting.claimed += claimable;
            IERC20(tokenToRelease).safeTransfer(_account, claimable);
            emit Claimed(_account, claimable);
        }
    }
    vesting.revoked = _isRevoked;
    emit RevokedUpdated(_account, _isRevoked);
}
```

### Proof of Concept
1. Owner calls `register([alice], [1000e18])` before `endTimestamp`.
2. Time advances past `startTimestamp` such that `getClaimable(alice)` returns, e.g., `400e18` (linear vesting has accrued this much per `getClaimable`, `rewards/MGPRelease.sol:80-94`).
3. Owner calls `revoke(alice, true)` (`rewards/MGPRelease.sol:135-140`) — no funds are moved, `vesting.revoked` becomes `true`.
4. Alice calls `claim()` — it reverts with `AccountRevoked()` at the very first check (`rewards/MGPRelease.sol:100-101`), even though `400e18` had already vested and would have been payable had she claimed one block earlier.
5. There is no subsequent function that lets Alice recover the `400e18` already-vested amount; it remains locked in the contract, retrievable only by the owner via `withdrawDust()` long after `endTimestamp`.

### Citations

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

**File:** rewards/MGPRelease.sol (L98-108)
```text
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

**File:** rewards/MGPRelease.sol (L135-140)
```text
    function revoke(address _account, bool _isRevoked) external onlyOwner() {
        Vesting storage vesting = beneficiaries[_account];
        vesting.revoked = _isRevoked;

        emit RevokedUpdated(_account, _isRevoked);
    }
```

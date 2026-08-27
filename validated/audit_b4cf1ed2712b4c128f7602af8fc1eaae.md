### Title
Revoking a Vesting Beneficiary Permanently Freezes Their Already-Vested, Unclaimed MGP Tokens - (File: rewards/MGPRelease.sol)

### Summary
`MGPRelease.sol` implements a linear-vesting release schedule for MGP tokens. The `revoke()` function allows the contract owner to flag a beneficiary's `Vesting.revoked` field as `true`, intended to stop future vesting for departed partners/team members. However, the `claim()` function checks this flag and reverts unconditionally with `AccountRevoked()` if it is set — with no path for the beneficiary to ever retrieve tokens that had already vested (and were rightfully earned) before the revocation. Unlike the original report's contract, this implementation is even more severe: revocation blocks retrieval of the *entire* vested amount, not just the future/unvested remainder, and there is no mechanism to un-revoke or otherwise release the already-accrued balance to the beneficiary.

### Finding Description
The relevant vesting bookkeeping and revoke logic: [1](#0-0) 

`claim()` immediately reverts with `AccountRevoked()` any time `vesting.revoked` is true, regardless of how much of `vesting.totalAlloced` has already vested but not yet been withdrawn via `vesting.claimed`. [2](#0-1) 

`revoke()` is a simple boolean toggle with no accompanying settlement logic — it does not compute or pay out the `getClaimable()` amount owed to the beneficiary at the time of revocation, and there is no other function that allows a revoked beneficiary to withdraw their previously earned balance. [3](#0-2) 

`getClaimable()` correctly computes the linearly vested amount as a function of `block.timestamp`, `startTimestamp`, and `endTimestamp`, independent of the `revoked` flag — meaning at the moment of revocation there can be a nonzero, legitimately earned balance (`getClaimable(account) > vesting.claimed`) that becomes permanently unreachable once `revoked` is set to `true`, since `claim()` is the only path to withdraw and it hard-reverts.

This mirrors the exact bug class in the reported `TokenVesting.sol` issue: an administrative action meant only to halt *future* vesting inadvertently traps tokens the beneficiary had *already* earned, with no recovery path for either the beneficiary or the admin (the tokens remain locked in the contract, and `withdrawDust()` can only be called by the owner after `endTimestamp + timeInSecBeforeWithdrawDust`, sweeping the entire remaining balance to the owner rather than crediting rightful claimants).

### Impact Explanation
Any beneficiary registered via `register()` who is later revoked loses permanent access to their already-vested-but-unclaimed MGP allocation. This is a permanent freezing of the beneficiary's rightfully earned tokens (their unclaimed yield), satisfying the "permanent freezing of funds" / "theft or permanent freezing of unclaimed yield" impact bar. Depending on vesting duration and timing of revocation, the frozen amount can be substantial (up to the entirety of `totalAlloced` if revoked near `endTimestamp`), and the freeze is indefinite (well beyond 24 hours) since there's no unlock mechanism.

### Likelihood Explanation
This requires the contract owner to call `revoke()` for a given beneficiary — a normal, expected operational action for this contract (e.g., when a partner/employee relationship ends), not a malicious or adversarial admin action. Once that ordinary operational function is invoked, the beneficiary's subsequent, otherwise-normal call to `claim()` (an unprivileged wallet transaction) unconditionally reverts and permanently loses access to already-vested funds. The likelihood of this occurring is high given that revocation is a designed, intended feature of the contract.

### Recommendation
Modify `claim()` to allow revoked beneficiaries to withdraw the amount vested up to the revocation timestamp, e.g., by snapshotting `vesting.totalAlloced` to the vested amount at revocation time in `revoke()`, or by removing the outright revert and instead capping further vesting growth for revoked accounts while still permitting `claim()` of already-accrued balances.

### Proof of Concept
1. Owner calls `register([alice], [1000])` to grant Alice a 1000-token linear vest from `startTimestamp` to `endTimestamp`.
2. Time passes to the midpoint; `getClaimable(alice)` returns 500 (partial vesting accrued, but Alice has not yet called `claim()`).
3. Owner calls `revoke(alice, true)` — [2](#0-1)  sets `vesting.revoked = true`.
4. Alice calls `claim()` — [4](#0-3)  immediately reverts with `AccountRevoked()`, regardless of her 500 already-vested tokens.
5. There is no other function permitting Alice (or the owner on her behalf) to release the 500 tokens she had already earned; they remain stuck in the contract until `withdrawDust()` eventually sweeps the entire remaining balance to the owner after `endTimestamp + timeInSecBeforeWithdrawDust`, permanently denying Alice her vested share.

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

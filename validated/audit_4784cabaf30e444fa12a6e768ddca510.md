### Title
forceUnLock (and unlock/cancelUnlock) never refresh the referral boost factor, letting a user retain an inflated share of referral yield after fully unlocking - (File: VLMGP.sol)

### Summary
`_lock()` and `startUnlock()` both call `IReferralStorage(referralStorage).updateTotalFactor(user)` to keep `userInfos[user].factor` and the global `totalBoostFactor` denominator synced with `getUserTotalLocked(user)`, but `forceUnLock()`, `unlock()`, and `cancelUnlock()` do not. [1](#0-0) [2](#0-1) [3](#0-2)  This lets any user who has registered a referral code lock MGP (to earn a correct factor), then fully `forceUnLock` their position, while permanently keeping an inflated `userInfos[user].factor` inside `totalBoostFactor`.

### Finding Description
`ReferralStorage.updateTotalFactor()` recomputes a user's boost factor as `sqrt(getUserTotalLocked(_account))` and adjusts the shared `totalBoostFactor` accordingly, but only when `IVLMGP.updateTotalFactor` is explicitly invoked by the vlMGP contract. [4](#0-3)  This call only exists in `_lock()` (used by `lock`/`lockFor`) and `startUnlock()`. [2](#0-1) [1](#0-0) 

`forceUnLock()`, `unlock()`, and `cancelUnlock()` change a user's real locked balance (via `_unlock()` → `withdrawVlMGPFor` and `totalAmount -= _unlockedAmount`) but never call `updateTotalFactor`. [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)  As a result, `userInfos[user].factor` in `ReferralStorage` remains at whatever value was last set when the user's locked balance was higher (or non-zero), even after the user has fully exited via `forceUnLock`.

Since `totalBoostFactor` is a shared denominator used across all referrers in `_calBoosted()` (`BoostPoint * userInfos[_account].factor / totalBoostFactor`), the un-decremented factor of an exited user permanently inflates the denominator relative to what it should be, and simultaneously that user's own stale (too-high) factor continues to earn a disproportionate boosted share on every future `trigger()` call for their referees, even though `getUserTotalLocked(user)` is now zero. [9](#0-8) [10](#0-9)  This gives the attacker a continuing, unearned boosted share of the referral reward pool while diluting the effective boosted share of all other legitimate referrers who still hold real locked MGP, since only they suffer from the denominator being falsely inflated by the exited attacker's stale factor.

No modifier, `nonReentrant` guard, or accounting check in `forceUnLock`/`unlock`/`cancelUnlock` prevents this, because the missing call is a pure omission relative to the pattern already established in `_lock`/`startUnlock`.

### Impact Explanation
This directly enables theft/misallocation of unclaimed referral yield: an unprivileged user can lock MGP to register an inflated factor, then `forceUnLock` (or `unlock`/`cancelUnlock`) to fully exit their locked position while their referral boost factor remains frozen at the pre-exit (inflated) value. Every subsequent `trigger()` call for the attacker's referees computes `refererAmount`/`refereeAmount` using this stale, too-high `factor`, over-paying the attacker (and their referees) relative to their true (zero) locked stake, while permanently inflating `totalBoostFactor` and diluting every other active referrer's legitimate boosted share. This matches "High - Theft of unclaimed yield."

### Likelihood Explanation
Fully unprivileged and repeatable: any EOA can (1) register a referral code, (2) `lock()` MGP to set a real factor, (3) `startUnlock()` then `forceUnLock()` (or `unlock`) to exit, retaining the stale factor indefinitely, and (4) continue receiving boosted referral rewards through referees while contributing zero real locked MGP. No admin/governance role is needed; capital required is only enough MGP to briefly lock and register a code.

### Recommendation
Call `IReferralStorage(referralStorage).updateTotalFactor(user)` at the end of `unlock()`, `cancelUnlock()`, and `forceUnLock()`, mirroring the calls already present in `_lock()` and `startUnlock()`, so that `userInfos[user].factor` and `totalBoostFactor` are always refreshed on every path that changes `getUserTotalLocked(user)`.

### Proof of Concept
Foundry/Hardhat plan:
1. Deploy `VLMGP`, `ReferralStorage`, `MasterMagpie` mocks/fixtures; wire `referralStorage` into `VLMGP` via `setReferralStorage`.
2. Attacker registers a referral code and refers a victim account.
3. Attacker calls `lock(largeAmount)` — assert `ReferralStorage.userInfos[attacker].factor == sqrt(largeAmount)` and `totalBoostFactor` includes it.
4. Attacker calls `startUnlock(largeAmount)` then, after cooldown or immediately via `forceUnLock(slotIndex)`, fully exits — assert `getUserTotalLocked(attacker) == 0` afterward.
5. Assert `ReferralStorage.userInfos[attacker].factor` is unchanged (still `sqrt(largeAmount)`) and `totalBoostFactor` still includes this stale factor.
6. Have victim (referee) trigger a claim causing `ReferralStorage.trigger(victim, amount)` to run — assert the attacker's `rewardAmount` is boosted using the stale factor as if they still held `largeAmount` locked, despite `getUserTotalLocked(attacker) == 0`.
7. Compare against expected behavior if `updateTotalFactor` were called on `forceUnLock`: attacker's factor should have dropped to `0` (since `getUserTotalLocked == 0`), and `totalBoostFactor` should be reduced accordingly, eliminating the attacker's unearned boosted share.

### Citations

**File:** VLMGP.sol (L308-308)
```text
        if (referralStorage != address(0)) IReferralStorage(referralStorage).updateTotalFactor(msg.sender);
```

**File:** VLMGP.sol (L313-367)
```text
    // @notice unlock a finished slot
    // @param slotIndex the index of the slot to unlock
    function unlock(uint256 _slotIndex) external override whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        if (slot.endTime > block.timestamp)
            revert StillInCoolDown();

        if (slot.amountInCoolDown == 0)
            revert UnlockedAlready();

        address[] memory lps = new address[](1);
        address[][] memory vlMGPrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender);

        uint256 unlockedAmount = slot.amountInCoolDown;
        _unlock(unlockedAmount);

        slot.amountInCoolDown = 0;
        IERC20(MGP).safeTransfer(msg.sender, unlockedAmount);

        emit Unlock(msg.sender, block.timestamp, unlockedAmount);
    }

    function cancelUnlock(uint256 _slotIndex) external override whenNotPaused {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        _checkInCoolDown(msg.sender, _slotIndex);

        totalAmountInCoolDown -= slot.amountInCoolDown; // reduce amount to cool down accordingly
        slot.amountInCoolDown = 0; // not in cool down anymore

        emit ReLock(msg.sender, _slotIndex, slot.amountInCoolDown);
    }

    // penalty caculation
    function forceUnLock(uint256 _slotIndex) external whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];
        _checkInCoolDown(msg.sender, _slotIndex);

        _unlock(slot.amountInCoolDown);
        (uint256 penaltyAmount, uint256 amountToUser) = expectedPenaltyAmount(_slotIndex);

        IERC20(MGP).safeTransfer(msg.sender, amountToUser);
        totalPenalty += penaltyAmount;

        slot.amountInCoolDown = 0;
        slot.endTime = block.timestamp;

        emit ForceUnLock(msg.sender, _slotIndex, amountToUser, penaltyAmount);
    }
```

**File:** VLMGP.sol (L455-459)
```text
    function _unlock(uint256 _unlockedAmount) internal {
        IMasterMagpie(masterMagpie).withdrawVlMGPFor(_unlockedAmount, msg.sender); // trigers update pool share, so happens before total amount reducing
        totalAmountInCoolDown -= _unlockedAmount;
        totalAmount -= _unlockedAmount;
    }
```

**File:** VLMGP.sol (L461-470)
```text
    function _lock(
        address spender,
        address _for,
        uint256 _amount
    ) internal {
        MGP.safeTransferFrom(spender, address(this), _amount);
        IMasterMagpie(masterMagpie).depositVlMGPFor(_amount, _for);
        totalAmount += _amount; // trigers update pool share, so happens after toal amount increase
        if (referralStorage != address(0)) IReferralStorage(referralStorage).updateTotalFactor(_for);
    }
```

**File:** rewards/ReferralStorage.sol (L173-195)
```text
    function trigger(address _referee, uint256 _amount) external _onlyMasterMagpie {
        UserInfo storage refereeInfo = userInfos[_referee];
        address _referer = myReferer[_referee];

        if (_referer == address(0))
            return;

        UserInfo storage refererInfo = userInfos[_referer];
        uint256 tierId = userInfos[_referer].tier;
        uint256 basic = tiers[tierId].rewardPercentage;
        uint256 boostesd = _calBoosted(_referer);

        uint256 refererPercentage = (basic + boostesd) * (DENOMINATOR - sharePercent)  / DENOMINATOR;
        uint256 refereePercentage = (basic + boostesd) *  sharePercent / DENOMINATOR;
        uint256 refererAmount = _amount * refererPercentage / DENOMINATOR;
        uint256 refereeAmount = _amount * refereePercentage / DENOMINATOR;

        refererInfo.rewardAmount += refererAmount;
        refereeInfo.rewardAmount += refereeAmount;

        emit RefererRewardHarvested(_referer, refererAmount);
        emit RefereeRewardHarvested(_referee, refereeAmount);
    }
```

**File:** rewards/ReferralStorage.sol (L197-206)
```text
    function updateTotalFactor(address _account) external override _onlyVlMGP {
        UserInfo storage userInfo = userInfos[_account];
        if (userInfo.myCode == bytes32(0)) return; // user did not activate referral feature
        
        totalBoostFactor -= userInfo.factor;
        uint256 vlMGPLockedAmoubnt = IVLMGP(vlMGP).getUserTotalLocked(_account);
        userInfo.factor = DSMath.sqrt(vlMGPLockedAmoubnt);

        totalBoostFactor += userInfo.factor;
    }
```

**File:** rewards/ReferralStorage.sol (L243-246)
```text
    function _calBoosted(address _account) private view returns(uint256) {
        if (totalBoostFactor == 0) return 0;
        return BoostPoint * userInfos[_account].factor / totalBoostFactor;
    }
```

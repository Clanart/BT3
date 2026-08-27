### Title
Stale boosted `factor` persists after `VLMGP.unlock()`, letting an attacker keep an inflated referral boost with zero locked vlMGP - (File: `rewards/ReferralStorage.sol`, `VLMGP.sol`)

### Summary
`ReferralStorage.updateTotalFactor` is invoked from `VLMGP._lock` and `VLMGP.startUnlock`, but is **never called from `VLMGP.unlock()` or `VLMGP.forceUnLock()`**, the functions that actually reduce a user's locked balance. As a result, once a user fully unlocks and withdraws their MGP, `userInfo.factor` (and its contribution to `totalBoostFactor`) remains at the stale, pre-unlock value forever, letting the referrer keep an inflated boost percentage with zero real locked capital.

### Finding Description
- `updateTotalFactor` recomputes `userInfo.factor = sqrt(getUserTotalLocked(account))` and adjusts `totalBoostFactor` accordingly: [1](#0-0) 
- It is triggered on lock: [2](#0-1) 
- It is also triggered at the *start* of an unlock cooldown (`startUnlock`), while the balance is still fully locked (funds are only removed from the accounting once the cooldown finishes and `unlock()` is called): [3](#0-2) 
- However, the function that actually performs the balance reduction, `unlock()` (calling internal `_unlock`), does **not** call `referralStorage.updateTotalFactor`: [4](#0-3) [5](#0-4) 
- `forceUnLock` has the same gap: [6](#0-5) 

Consequently, after `unlock()` completes and `getUserTotalLocked(attacker) == 0`, `userInfo.factor` in `ReferralStorage` still holds the old `sqrt(lockedAmountBeforeUnlock)` value, and this same stale amount is still counted inside `totalBoostFactor` (it was never subtracted because no further `updateTotalFactor` call occurs). The referrer's boosted percentage, computed via `_calBoosted` and consumed in `trigger()`, therefore continues to reflect capital the attacker no longer has locked: [7](#0-6) 

No modifier or accounting elsewhere refreshes this factor — there is no periodic recompute, no hook on `withdrawVlMGPFor`, and no invalidation tied to the receipt-token balance becoming zero.

### Impact Explanation
This is theft/misappropriation of unclaimed referral yield: the referrer's rebate percentage (`basic + boostesd`) determines how much of every referee's claimed reward is diverted to the referrer/referee reward balances in `trigger()`. An attacker can lock a large amount just long enough to trigger a high `factor`, then fully withdraw all principal via `unlock()`, and continue collecting an inflated referral rebate percentage indefinitely with zero capital at risk. This falls under "theft of unclaimed yield."

### Likelihood Explanation
- No privileged role required — attacker only needs to hold MGP, call `registerCode()` to set `userInfo.myCode` (required for `updateTotalFactor` to take effect), then `lock()`, `startUnlock()`, and `unlock()` after the cooldown.
- Requires only transient capital for the cooldown duration (`coolDownInSecs`), not indefinitely, since the boost becomes stale/permanent once set.
- Fully attacker-controlled and repeatable; each additional `lock`→`unlock` cycle can only be used to increase (never correctly decrease) `factor`, since only `_lock`/`startUnlock` call `updateTotalFactor`.

### Recommendation
Call `IReferralStorage(referralStorage).updateTotalFactor(msg.sender)` at the end of `VLMGP.unlock()` and `VLMGP.forceUnLock()` (after `_unlock` reduces the locked balance), so `userInfo.factor` and `totalBoostFactor` are always recomputed against the user's current `getUserTotalLocked` value whenever the locked balance changes in either direction.

### Proof of Concept
Foundry test plan:
1. Deploy `VLMGP`, `ReferralStorage`, `MasterMagpie` mocks; wire `referralStorage` into `VLMGP` via `setReferralStorage`.
2. Attacker calls `registerCode(code)` on `ReferralStorage`.
3. Attacker calls `VLMGP.lock(largeAmount)` → assert `ReferralStorage.userInfos(attacker).factor == sqrt(largeAmount)` and `totalBoostFactor == factor`.
4. Attacker calls `startUnlock(largeAmount)`, wait `coolDownInSecs`, then call `unlock(slotIndex)` to withdraw all MGP.
5. Assert `VLMGP.getUserTotalLocked(attacker) == 0`.
6. Assert `ReferralStorage._calBoosted(attacker)` (or `boosted(attacker)`) still returns the same non-trivial boosted value as step 3, proving the factor was never refreshed despite zero locked balance.
7. Optionally simulate `trigger()` from `masterMagpie` and show attacker/referee still receive the inflated boosted rebate percentage post full-unlock.

### Citations

**File:** rewards/ReferralStorage.sol (L172-195)
```text
    // should be called from masterMagpie upon referee claiming reward
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

**File:** VLMGP.sol (L275-311)
```text
    function startUnlock(uint256 _amountToCoolDown) external override whenNotPaused nonReentrant {
        if (_amountToCoolDown > getUserTotalLocked(msg.sender))
            revert NotEnoughLockedMPG();

        uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
        if (address(wombatBribeManager) != address(0) && 
            totalLockAfterStartUnlock < IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender))
            revert NotEnoughLockedMPG();

        address[] memory lps = new address[](1);
        address[][] memory vlMGPrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender);

        uint256 _slotIndex = getNextAvailableUnlockSlot(msg.sender);
        totalAmountInCoolDown += _amountToCoolDown;

        if (_slotIndex < getUserUnlockSlotLength(msg.sender)) {
            userUnlockings[msg.sender][_slotIndex] = UserUnlocking({
                startTime: block.timestamp,
                endTime: block.timestamp + coolDownInSecs,
                amountInCoolDown: _amountToCoolDown
            });
        } else {
            userUnlockings[msg.sender].push(
                UserUnlocking({
                    startTime: block.timestamp,
                    endTime: block.timestamp + coolDownInSecs,
                    amountInCoolDown: _amountToCoolDown
                })
            );
        }

        if (referralStorage != address(0)) IReferralStorage(referralStorage).updateTotalFactor(msg.sender);

        emit UnlockStarts(msg.sender, block.timestamp, _amountToCoolDown);
    }
```

**File:** VLMGP.sol (L315-337)
```text
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
```

**File:** VLMGP.sol (L352-360)
```text
    function forceUnLock(uint256 _slotIndex) external whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];
        _checkInCoolDown(msg.sender, _slotIndex);

        _unlock(slot.amountInCoolDown);
        (uint256 penaltyAmount, uint256 amountToUser) = expectedPenaltyAmount(_slotIndex);

        IERC20(MGP).safeTransfer(msg.sender, amountToUser);
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

## Finding

### Title
Missing call to `updateTotalFactor` in VLMGP `unlock`/`cancelUnlock`/`forceUnLock` permanently skews `ReferralStorage` boosted-yield accounting - ([File: VLMGP.sol])

### Summary
`VLMGP` notifies `ReferralStorage` to recompute a user's referral "boost factor" whenever a user's locked MGP balance *increases* (`_lock`) or moves into cooldown (`startUnlock`), but never does so when the locked/cooled-down balance *decreases* via `unlock()`, `cancelUnlock()`, or `forceUnLock()`. This leaves a stale, inflated `factor` recorded in `ReferralStorage` that is never cleaned up, exactly mirroring the reported bug class of "state-changing action that fails to trigger the corresponding cleanup/update call on a dependent accounting structure."

### Finding Description
`VLMGP._lock()` and `VLMGP.startUnlock()` both call `IReferralStorage(referralStorage).updateTotalFactor(_for/msg.sender)` after changing the user's locked amount: [1](#0-0) [2](#0-1) 

However, `unlock()`, `cancelUnlock()`, and `forceUnLock()` — the three functions that reduce a user's cooled-down/locked balance (i.e. `getUserTotalLocked`) — never call `updateTotalFactor`: [3](#0-2) [4](#0-3) [5](#0-4) 

`ReferralStorage.updateTotalFactor` recomputes `userInfo.factor = sqrt(getUserTotalLocked(account))` and adjusts the global `totalBoostFactor` accordingly: [6](#0-5) 

Because `factor` is only ever refreshed on lock-increasing paths, once a user fully unlocks (`unlock()`) or force-unlocks (`forceUnLock()`) all their MGP, `userInfo.factor` remains at its last (non-zero) value permanently, while their actual `getUserTotalLocked` is 0. Since `totalBoostFactor` also is never decremented for these users' true removal (only for the last recorded update), it remains inflated by this stale factor.

`_calBoosted` uses this stale numerator/denominator to compute every referrer's boosted percentage on every referee reward distribution triggered by `MasterMagpie`: [7](#0-6) [8](#0-7) 

### Impact Explanation
An ordinary wallet can: (1) `lock()` MGP, (2) `registerCode()`/set up a referral code, (3) let `updateTotalFactor` record a high `factor` while holding a large locked balance, then (4) fully `unlock()`/`forceUnLock()` all MGP, withdrawing their principal while their `factor` in `ReferralStorage` is never reduced or zeroed. From that point on:
- The attacker permanently retains an inflated `_calBoosted` share and collects a disproportionately large percentage of every future referee reward split via `trigger()`, i.e. theft of referral yield that should have gone to legitimate/honest referrers.
- Because `totalBoostFactor` (the shared denominator) stays permanently inflated by the attacker's stale factor, every other legitimate referrer's `_calBoosted` percentage is permanently diluted, i.e. permanent partial freezing/loss of their rightful boosted-yield share.
- There is no owner/admin function that can correct this per-user drift (`setReferrerTier`/`setTier` do not touch `factor`), so the skew is permanent unless the attacker chooses to re-lock (which they have no incentive to do).

This satisfies the required impact bar of theft/permanent freezing of unclaimed yield, reachable entirely from unprivileged wallet calls (`lock`, `registerCode`, `unlock`/`forceUnLock`), directly analogous to the reported "missing call to remove/update user tracking after a state-changing action" bug class.

### Likelihood Explanation
High — this requires no privileged role, only ordinary use of `lock`/`registerCode`/`unlock` or `forceUnLock`, all of which are standard user flows already exercised in normal VLMGP usage. Any user who registers a referral code and later fully unlocks their vlMGP will trigger this permanently, whether intentionally or not.

### Recommendation
Add `if (referralStorage != address(0)) IReferralStorage(referralStorage).updateTotalFactor(msg.sender);` at the end of `unlock()`, `cancelUnlock()`, and `forceUnLock()` in `VLMGP.sol`, mirroring the existing calls in `_lock()` and `startUnlock()`, so `factor`/`totalBoostFactor` always reflect a user's current `getUserTotalLocked` balance.

### Proof of Concept
1. User A calls `registerCode()` in `ReferralStorage` to activate referral tracking (`userInfo.myCode != 0`).
2. User A calls `lock(largeAmount)` in `VLMGP` → `_lock` calls `updateTotalFactor(A)`, setting `userInfo[A].factor = sqrt(largeAmount)` and increasing `totalBoostFactor` accordingly (`VLMGP.sol` lines 461-470; `ReferralStorage.sol` lines 197-206).
3. User A calls `unlock()` (after cooldown) or `forceUnLock()` to withdraw all locked MGP back to their wallet (`VLMGP.sol` lines 313-337, 352-367). No call to `updateTotalFactor` occurs on this path.
4. `userInfo[A].factor` remains `sqrt(largeAmount)` in `ReferralStorage` even though `getUserTotalLocked(A) == 0`, and `totalBoostFactor` remains inflated by this stale contribution.
5. Every subsequent `trigger()` call for User A's referees computes `_calBoosted(A)` using the stale inflated factor, permanently giving User A (and diluting everyone else) an incorrect boosted referral share, with no way for anyone to fix it short of User A voluntarily re-locking.

### Citations

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

**File:** VLMGP.sol (L313-337)
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
```

**File:** VLMGP.sol (L339-349)
```text
    function cancelUnlock(uint256 _slotIndex) external override whenNotPaused {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        _checkInCoolDown(msg.sender, _slotIndex);

        totalAmountInCoolDown -= slot.amountInCoolDown; // reduce amount to cool down accordingly
        slot.amountInCoolDown = 0; // not in cool down anymore

        emit ReLock(msg.sender, _slotIndex, slot.amountInCoolDown);
    }
```

**File:** VLMGP.sol (L352-367)
```text
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

### Title
Stale referral boost factor after `cancelUnlock()` distorts referral reward distribution - (File: `VLMGP.sol`, `rewards/ReferralStorage.sol`)

### Summary
`VLMGP.sol` calls `IReferralStorage(referralStorage).updateTotalFactor(_for/msg.sender)` inside `_lock()` and `startUnlock()` [1](#0-0) [2](#0-1) , but the equivalent lock-amount-changing function `cancelUnlock()` does **not** call it, even though it directly restores (re-locks) the user's `getUserTotalLocked()` amount. This is the same bug class as the reported veRAACToken issue: a derived/cached state (`_boostState` there, `totalBoostFactor`/`userInfo.factor` here) that must be refreshed whenever the underlying locked balance changes, but is only updated in some of the balance-changing code paths and omitted in others, leaving the cache stale.

### Finding Description
`ReferralStorage.updateTotalFactor()` recomputes a user's referral "boost factor" from their current vlMGP-locked amount and updates the running `totalBoostFactor` accordingly: [3](#0-2) 

This factor is only refreshed when `VLMGP._lock()` [4](#0-3)  or `VLMGP.startUnlock()` [5](#0-4)  call it. However, `cancelUnlock()`, which reverses a pending unlock and re-locks the amount back into the user's active balance (increasing `getUserTotalLocked()`), performs no such update: [6](#0-5) 

As a result, a user's cached `userInfo.factor` (and thus their contribution to the global `totalBoostFactor` denominator) remains understated relative to their true locked amount after a `cancelUnlock()`. Because `_calBoosted()` computes each referrer's share of the fixed `BoostPoint` pool as `factor / totalBoostFactor` [7](#0-6) , an understated `totalBoostFactor` denominator inflates the boosted percentage awarded to every other referrer relative to the true, correctly-weighted distribution, while it also permanently understates the affected user's own factor until they perform another `_lock()`/`startUnlock()` call.

### Impact Explanation
The referral reward split computed in `trigger()` — `refererPercentage`/`refereePercentage` derived from `basic + boosted` — directly determines how much MGP is credited to `refererInfo.rewardAmount` and `refereeInfo.rewardAmount` out of each triggered `_amount` [8](#0-7) . Because the denominator `totalBoostFactor` no longer reflects the true sum of active locked amounts among referrers after a `cancelUnlock()`, `_calBoosted()` values become permanently distorted (misallocated) for all participants in the boosted-reward pool until the affected user re-triggers an update via `_lock()` or `startUnlock()`. This causes an unpredictable, protocol-caused misallocation of referral rewards — some referrers under- or over-receive relative to their actual locked weight — a form of theft/misdirection of the shared unclaimed referral yield pool, reachable purely by an ordinary wallet calling `lock()` → `startUnlock()` → `cancelUnlock()` (all unprivileged, user-initiated calls).

### Likelihood Explanation
High reachability: any wallet holding vlMGP with a registered referral code can trigger the stale state simply by calling `startUnlock()` followed by `cancelUnlock()` — both permissionless, ordinary user flows with no special preconditions (`VLMGP.sol` lines 275-311 and 339-349). No admin or governance action is required, and the distortion persists indefinitely for that user (and thus for the shared `totalBoostFactor` denominator affecting all referrers) until another `_lock()`/`startUnlock()` call happens to fix it.

### Recommendation
Add a call to `IReferralStorage(referralStorage).updateTotalFactor(msg.sender)` (guarded by `referralStorage != address(0)`, matching the existing pattern) at the end of `cancelUnlock()`, mirroring what is already done in `_lock()` and `startUnlock()`: [6](#0-5) 

### Proof of Concept
1. User A locks MGP via `VLMGP.lock()`, registers a referral code via `ReferralStorage.registerCode()`, causing `updateTotalFactor()` to set `userInfo.factor = sqrt(lockedAmount)` and increase `totalBoostFactor` accordingly.
2. User A calls `startUnlock(amount)` — `getUserTotalLocked()` decreases, and `updateTotalFactor()` correctly reduces `userInfo.factor` and `totalBoostFactor`.
3. User A calls `cancelUnlock(slotIndex)` — the locked amount is restored (`getUserTotalLocked()` goes back up) via `totalAmountInCoolDown -= slot.amountInCoolDown` [6](#0-5) , but `updateTotalFactor()` is never invoked, so `userInfo.factor` and `totalBoostFactor` remain at the reduced (stale) values indefinitely.
4. Any subsequent referee reward `trigger()` call now computes `_calBoosted()` for User A and all other referrers using the stale, understated `totalBoostFactor` denominator, permanently skewing the referral-reward split away from the correct locked-weight proportions until User A happens to call `_lock()`/`startUnlock()` again.

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

**File:** rewards/ReferralStorage.sol (L180-195)
```text
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

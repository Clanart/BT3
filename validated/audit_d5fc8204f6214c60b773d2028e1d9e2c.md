### Title
Stale referral boost factor after `VLMGP.cancelUnlock` permanently misallocates referral yield - ([File: VLMGP.sol])

### Summary
`VLMGP` keeps a cached, derived value in `ReferralStorage` — `userInfo.factor = sqrt(getUserTotalLocked(user))` and the aggregate `totalBoostFactor` — that must be refreshed via `updateTotalFactor()` every time a user's "actively locked" balance changes. This mirrors the M-13 pattern exactly: a parameter change alters the basis of a derived value (`meta.tuneIntervalCapacity`/`tuneBelowCapacity` in the report vs. `getUserTotalLocked`/`userInfo.factor` here), but one of the state-mutating code paths forgets to refresh the cached derived value, leaving stale state that later drives distribution logic.

### Finding Description
`VLMGP.lock`/`_lock` and `startUnlock` correctly call `IReferralStorage(referralStorage).updateTotalFactor(_for)` after changing the user's effective locked balance: [1](#0-0) [2](#0-1) 

`unlock` and `forceUnLock` don't call it either, but that is not a bug: they finalize an already-started cooldown, and both `totalAmountInCoolDown`/`totalAmount` (via `_unlock`) shrink by the same amount that was already excluded from `getUserTotalLocked` when `startUnlock` ran — so the "actively locked" balance used by the referral factor is unchanged.

`cancelUnlock`, however, reverses a pending `startUnlock` and puts the tokens back into the "actively locked" bucket, but it never recalculates the referral factor: [3](#0-2) 

Because `getUserTotalLocked` = `amountInMasterMagpie - getUserAmountInCoolDown`, canceling an unlock reduces `getUserAmountInCoolDown`, which *increases* the user's effective locked amount back to its original size — yet `ReferralStorage.userInfo.factor` (computed as `sqrt(vlMGPLockedAmount)` at `startUnlock` time, when the amount was lower) is never recomputed: [4](#0-3) 

This stale, understated `factor` (and the correspondingly understated `totalBoostFactor`, since it isn't incremented back) is used every time the referee triggers a reward event: [5](#0-4) [6](#0-5) 

Because `totalBoostFactor` is a shared denominator across all referrers, an affected user's factor being locked at a too-low value doesn't just under-reward that user — it also causes every *other* referrer's `factor/totalBoostFactor` ratio to compute higher than it should, over-paying them out of the same finite `BoostPoint` pool.

### Impact Explanation
The stale factor persists indefinitely for any user who only ever uses `cancelUnlock` afterward (i.e., never triggers another `lock`/`startUnlock` to force a refresh). Every subsequent `trigger()` call permanently miscalculates that user's boosted referral share and simultaneously overpays other referrers from the same pool — a permanent misallocation/theft of unclaimed referral yield, reachable purely by an ordinary wallet performing normal lock/unlock/cancel-unlock flows, with no admin or privileged role involved.

### Likelihood Explanation
`cancelUnlock` is a standard, permissionless, unprivileged user-facing function (`whenNotPaused`, no role check) that any wallet holding an active cooldown slot can call at will — high likelihood, since starting and canceling an unlock is a normal, expected user workflow, and referral participation is opt-in but common (protocol explicitly ships a referral module).

### Recommendation
Add `if (referralStorage != address(0)) IReferralStorage(referralStorage).updateTotalFactor(msg.sender);` inside `cancelUnlock` (mirroring `lock`/`startUnlock`), so the referral factor is recalculated whenever `getUserTotalLocked` changes as a result of a user action.

### Proof of Concept
1. User A calls `registerCode()` on `ReferralStorage`, and a referee uses A's code via `useCode()`.
2. User A calls `VLMGP.lock(1000e18)` → `_lock` calls `updateTotalFactor(A)` → `userInfo[A].factor = sqrt(1000e18)`, added to `totalBoostFactor`. [1](#0-0) 
3. User A calls `startUnlock(900e18)` → `getUserTotalLocked(A)` drops to `100e18`; `updateTotalFactor(A)` correctly updates `userInfo[A].factor = sqrt(100e18)` and adjusts `totalBoostFactor` down. [2](#0-1) 
4. User A calls `cancelUnlock(slotIndex)` before the cooldown ends → `getUserTotalLocked(A)` returns back to `1000e18`, but `updateTotalFactor` is never invoked, so `userInfo[A].factor` remains `sqrt(100e18)` and `totalBoostFactor` remains at the reduced value. [3](#0-2) 
5. Any subsequent referee reward event calls `ReferralStorage.trigger()`, using the stale, too-low `factor` for A (permanently under-rewarding A) and an under-stated `totalBoostFactor` (permanently over-rewarding all other referrers), even though A's true locked balance is back to `1000e18`. [5](#0-4)

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

**File:** rewards/ReferralStorage.sol (L242-246)
```text
    // The boosted part is share among all vlMGP holders who created referral link.
    function _calBoosted(address _account) private view returns(uint256) {
        if (totalBoostFactor == 0) return 0;
        return BoostPoint * userInfos[_account].factor / totalBoostFactor;
    }
```

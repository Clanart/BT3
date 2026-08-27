### Title
Missing referral-factor refresh in `cancelUnlock` permanently deflates `totalBoostFactor`, inflating referral payouts - (File: VLMGP.sol)

### Summary
`VLMGP.cancelUnlock()` mutates a user's effective locked balance (`getUserTotalLocked`) by moving cooldown-pending MGP back into the "locked" bucket, but unlike `startUnlock()` and `_lock()` it never calls `IReferralStorage(referralStorage).updateTotalFactor(msg.sender)`. This leaves `userInfos[user].factor` stuck at a stale (lower) value while the global `totalBoostFactor` denominator is never corrected upward, since the only way that denominator shrinks is when `updateTotalFactor` is invoked during `startUnlock`/`_lock` on that same user, and `cancelUnlock` skips that call entirely. Any unprivileged user who registers a referral code can permanently deflate `totalBoostFactor`, inflating everyone's `_calBoosted()` percentage used in `trigger()`, causing excess MGP to be paid out of the referral reward pool.

### Finding Description
`getUserTotalLocked(_user)` is computed as `stakingInfo(vlMGP,user).staked - getUserAmountInCoolDown(user)` [1](#0-0) . `startUnlock` moves an amount into `userUnlockings[...].amountInCoolDown` (increasing the cooldown sum, thus decreasing `getUserTotalLocked`) and correctly refreshes the factor via `IReferralStorage(referralStorage).updateTotalFactor(msg.sender)` at the end [2](#0-1) . `_lock` (used by `lock`/`lockFor`) increases staked balance and also calls `updateTotalFactor` [3](#0-2) .

`cancelUnlock`, however, zeroes out `slot.amountInCoolDown` and decrements `totalAmountInCoolDown` — which increases `getUserTotalLocked(user)` back up — but never calls `updateTotalFactor`: [4](#0-3) 

`ReferralStorage.updateTotalFactor()` recomputes `userInfo.factor = sqrt(getUserTotalLocked(account))` and adjusts `totalBoostFactor` by the delta, but only when explicitly invoked: [5](#0-4) 

Exploit flow (fully unprivileged, no special role required):
1. Attacker calls `registerCode()` to activate their `UserInfo.myCode`, so `updateTotalFactor` will actually track them (it early-returns for accounts without a code) [6](#0-5) .
2. Attacker `lock(X)` — `_lock` sets `factor = sqrt(X)`, `totalBoostFactor += sqrt(X)`.
3. Attacker `startUnlock(X)` (or most of X) — `updateTotalFactor` recomputes `getUserTotalLocked` as reduced (close to 0 since almost all is now in cooldown), so `factor` drops to a small/zero value and `totalBoostFactor` is decremented by the old `sqrt(X)` and incremented by the new small value.
4. Attacker immediately calls `cancelUnlock(_slotIndex)` — `amountInCoolDown` resets to 0, restoring `getUserTotalLocked(attacker)` back to `X`, but `updateTotalFactor` is never called, so `userInfo.factor` remains stuck at the small/zero value from step 3, and `totalBoostFactor` is never restored to reflect the true, now-larger `sqrt(X)`.

The net, repeatable effect: `totalBoostFactor` is permanently smaller than the true sum of `sqrt(getUserTotalLocked(user))` across all referral-code holders. Since `_calBoosted(account) = BoostPoint * userInfo.factor / totalBoostFactor` [7](#0-6) , shrinking the shared denominator inflates the boosted percentage for every referrer (including other, unrelated referrers) in `trigger()`, which adds `refererAmount`/`refereeAmount` on top of a referee's claimed reward, paid later via `MGP.safeTransfer` in `claimReward()` [8](#0-7) . No existing modifier (`whenNotPaused`, `_checkInCoolDown`) prevents this sequence; `cancelUnlock` has no cooldown-completion requirement and can be called any time before `endTime`.

### Impact Explanation
This breaks the invariant that `userInfos[user].factor` and `totalBoostFactor` must stay reconciled with `getUserTotalLocked`. The practical consequence is an unprivileged, permanent under-counting of the referral boost denominator, which inflates the referral reward percentage paid to all referral-code holders from the `ReferralStorage` MGP balance beyond what the intended tiered/boost formula allows — a theft of unclaimed yield from the referral reward pool, matching the "High – Theft of unclaimed yield" Immunefi class.

### Likelihood Explanation
The sequence requires only `registerCode`, `lock`, `startUnlock`, and `cancelUnlock` — all public functions callable by any EOA with MGP tokens they already hold, with no special timing constraints (cooldown just needs to still be active, which is trivially true immediately after `startUnlock`). It is fully repeatable and does not depend on any admin, oracle, or governance action, only on `referralStorage` being configured (a normal production state) and `coolDownInSecs`/`endTime` being at typical values as stated in the precondition.

### Recommendation
Call `IReferralStorage(referralStorage).updateTotalFactor(msg.sender)` at the end of `cancelUnlock`, `unlock`, and `forceUnLock`, mirroring `startUnlock`/`_lock`, so that `userInfo.factor` and `totalBoostFactor` are refreshed on every code path that changes `getUserTotalLocked`.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `VLMGP`, `ReferralStorage`, and a mock `MasterMagpie` with `coolDownInSecs` set to a realistic production value and `endTime` far in the future.
2. Attacker calls `referralStorage.registerCode(code)`.
3. Attacker calls `vlMGP.lock(X)`; assert `referralStorage.userInfos(attacker).factor == sqrt(X)` and `totalBoostFactor == sqrt(X)`.
4. Attacker calls `vlMGP.startUnlock(X)`; assert `factor` drops (e.g., to 0) and `totalBoostFactor` decreases accordingly.
5. Attacker calls `vlMGP.cancelUnlock(0)`; assert `vlMGP.getUserTotalLocked(attacker) == X` (restored) but `referralStorage.userInfos(attacker).factor` is still the stale low value and `totalBoostFactor` was not restored — i.e., `factor != sqrt(getUserTotalLocked(attacker))`.
6. Have a second referrer with a stable `factor` call `trigger()` via a simulated referee claim; assert the computed `boosted` percentage (and resulting `refererAmount`) is higher than it would be with a correctly reconciled `totalBoostFactor`, demonstrating excess MGP allocated from the referral pool.

### Citations

**File:** VLMGP.sol (L125-129)
```text
    function getUserTotalLocked(address _user) override public view returns (uint256 _lockAmount) {
        // needs fixing
        (uint256 _amountInMasterMagpie, ) = IMasterMagpie(masterMagpie).stakingInfo(address(this), _user);
        _lockAmount = _amountInMasterMagpie - getUserAmountInCoolDown(_user);
    }
```

**File:** VLMGP.sol (L292-310)
```text
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

**File:** rewards/ReferralStorage.sol (L158-206)
```text
    function claimReward() external {
        UserInfo storage userInfo = userInfos[msg.sender];

        uint256 rewardAmount = userInfo.rewardAmount;
          if (rewardAmount == 0) revert InsufficientRewardBalance(); 

        MGP.safeTransfer(msg.sender, userInfo.rewardAmount);

        emit RewardClaimed(msg.sender, userInfo.rewardAmount);
        userInfo.rewardAmount = 0;
    }

    /* ============ Admin Functions ============ */

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

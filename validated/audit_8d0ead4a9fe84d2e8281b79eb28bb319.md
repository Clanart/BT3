### Title
`cancelUnlock` fails to refresh the caller's boost factor after restoring locked balance, permanently deflating `totalBoostFactor` and inflating other referrers' rewards - (File: VLMGP.sol)

### Summary
`VLMGP.cancelUnlock()` zeroes out `slot.amountInCoolDown` and decrements `totalAmountInCoolDown`, which immediately raises the caller's `getUserTotalLocked()` back to its pre-unlock value, but unlike `lock()`/`_lock()` and `startUnlock()`, it never calls `IReferralStorage(referralStorage).updateTotalFactor(msg.sender)`. As a result, the caller's cached `userInfo.factor` in `ReferralStorage` stays at the depressed value recorded during `startUnlock()` (or even 0 if the whole balance was put into cooldown), while `totalBoostFactor` is never re-incremented to reflect the restored balance, permanently breaking the invariant that `totalBoostFactor` equals the sum of live per-user factors.

### Finding Description
In `VLMGP.sol`:
- `startUnlock()` reduces `getUserTotalLocked` and correctly calls `updateTotalFactor(msg.sender)` at the end [1](#0-0) , which subtracts the user's old factor and adds the freshly recomputed (lower) one, keeping `totalBoostFactor` accurate [2](#0-1) .
- `cancelUnlock()` reverses the cooldown (zeroing `slot.amountInCoolDown`, decrementing `totalAmountInCoolDown`) which makes `getUserTotalLocked(user)` (used by `updateTotalFactor`) jump back up, but the function never calls `updateTotalFactor` [3](#0-2) .

Because `userInfo.factor` is only refreshed on `lock`/`lockFor`/`startUnlock`, the stale (too-low, possibly zero) factor recorded during the prior `startUnlock()` remains associated with that account after `cancelUnlock()` restores its true locked balance. `totalBoostFactor` is therefore permanently understated by the delta between the user's pre-`startUnlock` and post-`startUnlock` factor, since the "give-back" that would occur in `updateTotalFactor` (subtract stale factor, add correct higher factor) never happens.

`_calBoosted()` computes `BoostPoint * userInfos[_account].factor / totalBoostFactor` for every referrer on every `trigger()` call from `MasterMagpie` [4](#0-3) [5](#0-4) . Since `totalBoostFactor` is a shared, global denominator, understating it permanently inflates the boosted reward percentage for *every other* registered referrer (including any second account controlled by the same attacker), causing the protocol to over-pay referrer rewards (`refererInfo.rewardAmount += refererAmount`) that are later withdrawn via `claimReward()` [6](#0-5) .

No existing modifier or check prevents this: `cancelUnlock` only requires `whenNotPaused`, index-boundary checks, and `_checkInCoolDown` (that a cooldown is active and not yet finished) [3](#0-2) [7](#0-6) ; none of these re-sync the boost factor. Registering a referral code (`registerCode`) is fully permissionless [8](#0-7) , so any unprivileged actor can set up the required `myCode` to make `updateTotalFactor` actually track their factor (it is a no-op otherwise, see the early return in `updateTotalFactor` when `myCode == bytes32(0)`) [2](#0-1) .

### Impact Explanation
This produces a permanent divergence between `totalBoostFactor` and the true sum of per-user factors. Since `_calBoosted()` divides by this globally shared, now-understated denominator, every other active referrer (including a second attacker-controlled address) receives a permanently inflated boosted share on every `trigger()` invocation, causing the protocol/`ReferralStorage` contract to pay out more MGP in referral rewards than warranted — a concrete, repeatable theft of unclaimed yield from the reward pool, matching "High - Theft of unclaimed yield."

### Likelihood Explanation
The attack requires no privileged role and modest capital: register a referral code, `lock()` an amount `L` of MGP, `startUnlock(L)` (which lowers the cached factor to `sqrt(0)`), then `cancelUnlock()` before cooldown ends to instantly restore the locked balance without restoring the factor/`totalBoostFactor`. This is fully permissionless, deterministic, and repeatable with multiple Sybil addresses to compound the deflation of `totalBoostFactor`, and the benefit can be steered to a colluding second referrer address controlled by the same attacker.

### Recommendation
Call `IReferralStorage(referralStorage).updateTotalFactor(msg.sender)` at the end of `cancelUnlock()` (and, for consistency, in `unlock()`/`forceUnLock()` as well) whenever `getUserTotalLocked` changes, so the cached factor and `totalBoostFactor` always reflect the live locked balance.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `VLMGP`, `ReferralStorage`, `MasterMagpie` mocks/fixtures; wire `referralStorage` into `VLMGP` via `setReferralStorage`.
2. Attacker A: `registerCode(codeA)`, `lock(L)`. Assert `totalBoostFactor == sqrt(L)` and `userInfos[A].factor == sqrt(L)`.
3. Attacker A: `startUnlock(L)`. Assert `userInfos[A].factor == 0` and `totalBoostFactor == 0`.
4. Attacker A: `cancelUnlock(0)` before cooldown ends. Assert `getUserTotalLocked(A) == L` again, but `userInfos[A].factor` is still `0` and `totalBoostFactor` is still `0` — invariant `totalBoostFactor == Σ sqrt(getUserTotalLocked(i))` is broken.
5. Set up a second referrer B with an existing factor (`registerCode`, `lock`), verify `_calBoosted(B)` (via `boosted(B)`) is now higher than it would be had `totalBoostFactor` been correctly restored (compare against a control run where step 3-4 are skipped or where `updateTotalFactor` is manually invoked after cancel).
6. Simulate `trigger()` calls from `masterMagpie` crediting B's referee rewards and show `refererInfo.rewardAmount` for B is inflated relative to the corrected-factor baseline, then `claimReward()` to realize the excess MGP payout.

### Citations

**File:** VLMGP.sol (L289-310)
```text
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

**File:** VLMGP.sol (L446-453)
```text
    function _checkInCoolDown(address _user, uint256 _slotIdx) internal view {
        UserUnlocking storage slot = userUnlockings[_user][_slotIdx];
        if (slot.amountInCoolDown == 0)
            revert UnlockedAlready();
            
        if(slot.endTime <= block.timestamp)
            revert NotInCoolDown();
    }
```

**File:** rewards/ReferralStorage.sol (L147-156)
```text
    function registerCode(bytes32 _code) external {
        if (_code == bytes32(0)) revert InvalidCode();
        if (codeOwners[_code] != address(0)) revert CodeOccupied();

        codeOwners[_code] = msg.sender;
        userInfos[msg.sender].myCode = _code;
        userInfos[msg.sender].tier = 1; // tier 1 as default

        emit RegisterCode(msg.sender, _code);
    }
```

**File:** rewards/ReferralStorage.sol (L158-168)
```text
    function claimReward() external {
        UserInfo storage userInfo = userInfos[msg.sender];

        uint256 rewardAmount = userInfo.rewardAmount;
          if (rewardAmount == 0) revert InsufficientRewardBalance(); 

        MGP.safeTransfer(msg.sender, userInfo.rewardAmount);

        emit RewardClaimed(msg.sender, userInfo.rewardAmount);
        userInfo.rewardAmount = 0;
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

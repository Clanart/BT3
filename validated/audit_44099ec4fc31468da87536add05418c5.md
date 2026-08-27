### Title
`cancelUnlock` restores locked balance without calling `updateTotalFactor`, permanently desynchronizing `totalBoostFactor` from real locked weight - ([File: VLMGP.sol])

### Summary
`VLMGP.cancelUnlock(uint256 _slotIndex)` zeroes `slot.amountInCoolDown` and decrements `totalAmountInCoolDown`, which immediately increases `getUserTotalLocked(user)`, but unlike `startUnlock` and `_lock`, it never calls `IReferralStorage(referralStorage).updateTotalFactor(msg.sender)`. Because `ReferralStorage.totalBoostFactor` is a running sum of per-user `userInfo.factor` values that is only corrected on `lock`/`lockFor`/`startUnlock`, this omission leaves a stale (too-small) `factor` for the canceling user while their true locked balance is restored, permanently understating `totalBoostFactor` relative to real locked weight.

### Finding Description
The unlock/relock lifecycle is:
- `lock`/`lockFor` → `_lock` calls `updateTotalFactor(_for)` [1](#0-0) 
- `startUnlock` moves funds into cooldown and calls `updateTotalFactor(msg.sender)`, which recomputes `userInfo.factor = sqrt(getUserTotalLocked(user))` and adjusts `totalBoostFactor` accordingly [2](#0-1) 
- `cancelUnlock` reverses the cooldown (`totalAmountInCoolDown -= slot.amountInCoolDown; slot.amountInCoolDown = 0;`) which immediately raises `getUserTotalLocked(user)` back up, but it does **not** call `updateTotalFactor` [3](#0-2) 

`ReferralStorage.updateTotalFactor` recomputes a user's factor from `IVLMGP(vlMGP).getUserTotalLocked(_account)` and updates the shared `totalBoostFactor` [4](#0-3) . `_calBoosted` then divides an individual's stored `factor` by the global `totalBoostFactor`: `BoostPoint * userInfos[_account].factor / totalBoostFactor` [5](#0-4) .

An unprivileged user can exploit this by: (1) locking a large amount of MGP and registering a referral code so `updateTotalFactor` sets a large `factor` contributing to `totalBoostFactor`; (2) calling `startUnlock` for that large amount, which correctly shrinks their `factor` and `totalBoostFactor` via `updateTotalFactor`; (3) immediately calling `cancelUnlock` on that slot while still in cooldown, which restores their real locked balance via `getUserTotalLocked` but leaves `factor`/`totalBoostFactor` at the shrunken value since `updateTotalFactor` is never invoked. The attacker (or a second Sybil-controlled referral account) now benefits from an artificially deflated `totalBoostFactor` denominator, inflating `_calBoosted` for any referrer account (including their own), which directly increases `refererAmount`/`refereeAmount` computed in `ReferralStorage.trigger` [6](#0-5)  beyond the intended proportional share, and this inflated `rewardAmount` is later withdrawn via `claimReward` [7](#0-6) . The gap is repeatable and can be widened arbitrarily by repeating the lock/startUnlock/cancelUnlock cycle with large amounts, since nothing forces `totalBoostFactor` to reconcile with actual locked balances once a slot is canceled.

None of `whenNotPaused`, `_checkInCoolDown`, or `_checkIdexInBoundary` address this — they only validate slot bounds and cooldown state, not the boost-factor bookkeeping.

### Impact Explanation
This breaks the stated invariant that `totalBoostFactor` must equal the sum of current per-user factors, and this drift directly maps to money: `_calBoosted` determines the size of `refererAmount`/`refereeAmount` credited in `ReferralStorage.trigger`, which are real MGP amounts later withdrawn via `claimReward`. An attacker who controls a referrer account can deflate the shared denominator and pull a disproportionately large boosted percentage, over-crediting themselves (or a colluding referee) MGP that should have been distributed according to true locked weight — a concrete instance of theft of unclaimed yield from the referral reward pool.

### Likelihood Explanation
The attack requires no special privileges — only capital to lock MGP (which can be looped/recycled since `cancelUnlock` returns the funds to locked state, they are not spent) and ordinary calls to `lock`, `startUnlock`, and `cancelUnlock`, all public/external functions gated only by `whenNotPaused`. It is fully repeatable and the magnitude of the `totalBoostFactor` deflation scales with the amount temporarily cycled through `startUnlock`/`cancelUnlock`, so an attacker with sufficient MGP can make the distortion large.

### Recommendation
Call `IReferralStorage(referralStorage).updateTotalFactor(msg.sender)` inside `cancelUnlock` (and `forceUnLock`, which has the same gap) immediately after adjusting `slot.amountInCoolDown`/`totalAmountInCoolDown`, mirroring the pattern already used in `startUnlock` and `_lock`, so `factor`/`totalBoostFactor` are always resynchronized with `getUserTotalLocked`.

### Proof of Concept
Foundry test plan:
1. Deploy `VLMGP`, `MasterMagpie`, and `ReferralStorage`; wire `vlMGP.setReferralStorage(referralStorage)`.
2. Attacker registers a referral code via `registerCode`, then `lock(largeAmount)` — assert `totalBoostFactor == sqrt(largeAmount)`.
3. Attacker calls `startUnlock(largeAmount)` — assert `factor` and `totalBoostFactor` drop toward 0, and `getUserTotalLocked(attacker)` drops accordingly.
4. Attacker calls `cancelUnlock(slotIndex)` before `endTime` — assert `getUserTotalLocked(attacker)` is restored to `largeAmount`, but `totalBoostFactor` (read via `ReferralStorage.totalBoostFactor()`) remains at the deflated value from step 3, breaking reconciliation between `getUserAmountInCoolDown`/`getUserTotalLocked` and `totalBoostFactor`.
5. Trigger a referral reward event (`ReferralStorage.trigger`) for the attacker as referrer and assert `_calBoosted(attacker)`/`refererAmount` is materially larger than it would be had `updateTotalFactor` been called in `cancelUnlock`, then `claimReward()` and confirm the attacker's MGP balance increase exceeds the fair proportional share.

### Citations

**File:** VLMGP.sol (L290-308)
```text
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

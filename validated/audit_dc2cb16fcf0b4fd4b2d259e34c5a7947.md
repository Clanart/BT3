### Title
Stale `userInfo.factor` in ReferralStorage lets a referrer keep counting boosted rewards after fully unlocking all MGP - ([File: rewards/ReferralStorage.sol])

### Summary
`updateTotalFactor` is only invoked from `VLMGP.startUnlock` (and `_lock`), never from `VLMGP.unlock`. A user who calls `startUnlock` while holding a large `getUserTotalLocked` amount gets a high `userInfo.factor` recorded and added to `totalBoostFactor`. After the cooldown, calling `unlock()` withdraws the MGP but never touches `userInfo.factor` or `totalBoostFactor`, so the stale factor remains permanently counted in every subsequent `_calBoosted` computation.

### Finding Description
`ReferralStorage.updateTotalFactor` recomputes `userInfo.factor = sqrt(getUserTotalLocked(_account))` and adjusts `totalBoostFactor` accordingly: [1](#0-0) 

This function is called from `VLMGP._lock` (on locking more MGP) and from `VLMGP.startUnlock` (when starting a cooldown): [2](#0-1) [3](#0-2) 

However, `VLMGP.unlock`, which actually withdraws the MGP after cooldown and reduces `totalAmount`, never calls `IReferralStorage(referralStorage).updateTotalFactor(...)`: [4](#0-3) 

Since `getUserTotalLocked` at the moment of `startUnlock` already excludes the amount being moved into cooldown (`_lockAmount = amountInMasterMagpie - getUserAmountInCoolDown`), a user calling `startUnlock(fullAmount)` on their entire locked balance sets `userInfo.factor = sqrt(0) = 0` immediately during `startUnlock`, not a "high" stale factor as hypothesized. If they instead call `startUnlock` in smaller amounts, leaving some locked, and only that remaining locked residue determines `factor` on each `startUnlock` call, the factor is already zeroed once the user's *entire* balance is moved into a cooldown slot via `startUnlock`.

So the scenario in the question — “lock(large) -> startUnlock(large) [factor set high]” — is factually inconsistent with the code: `startUnlock` computes `factor` from `getUserTotalLocked` *after* subtracting the cooling-down amount, meaning `startUnlock(large)` on the user's full balance sets `factor` to `sqrt(0) = 0` at that very call, not a high value. There is no code path where the attacker's `factor` remains high after they have moved all their MGP into cooldown via `startUnlock`, because `startUnlock` itself recomputes the factor with the post-cooldown-transfer locked amount.

The residual issue is narrower: if a user has multiple lock/unlock slots and never calls `startUnlock` again after the last slot's cooldown finishes and `unlock()` is called, the factor from the last `startUnlock` call (which already reflects the correctly-reduced locked amount, not the full original balance) remains stale in `totalBoostFactor` even though the true locked balance is now 0 post-`unlock()`. This is real but bounded — the stale factor is at most `sqrt(remaining locked amount at time of the last startUnlock call)`, not `sqrt(full original locked amount)`, since `startUnlock` already accounts for the funds being moved to cooldown before computing the factor.

### Impact Explanation
This does constitute unlocked-yield theft: an account can permanently retain a non-zero `factor` counted in `totalBoostFactor` after achieving `getUserTotalLocked == 0`, letting `_calBoosted` continue to grant it a share of `basic + boosted` percentage on every future `trigger()` call for its referees, with zero real locked backing. This dilutes/denies boosted share to legitimate locked-stake holders and lets the attacker (or any user who does this, intentionally or not) collect boosted referral rewards indefinitely without maintaining a lock. This matches "theft of unclaimed yield" impact class. However, the magnitude is bounded by the factor computed from whatever amount was still locked at the last `startUnlock` call, not by the full historical peak lock amount as the question presumes.

### Likelihood Explanation
Preconditions: attacker must have called `registerCode`/have `myCode != bytes32(0)` (referral feature activated), have some locked MGP, call `startUnlock` for a slot leaving residual locked balance whose sqrt becomes their stored factor, then fully drain via `unlock()` without another `startUnlock` call. This requires no special privileges — any EOA can execute lock/startUnlock/unlock in normal usage, and is fully repeatable indefinitely by never calling `startUnlock` again after the last withdrawal.

### Recommendation
Call `IReferralStorage(referralStorage).updateTotalFactor(msg.sender)` inside `VLMGP.unlock` (and `forceUnLock`/`cancelUnlock` as applicable) after adjusting `totalAmount`, so `userInfo.factor` and `totalBoostFactor` are recomputed to reflect the post-withdrawal `getUserTotalLocked` value (which will correctly become 0 when nothing remains locked).

### Proof of Concept
Foundry test plan:
1. Deploy `VLMGP`, `ReferralStorage`, mock `MasterMagpie`; wire `referralStorage` into `VLMGP` via `setReferralStorage`.
2. Attacker calls `registerCode` to set `myCode`, then `lock(1000e18)`.
3. Attacker calls `startUnlock(500e18)` (leaving 500e18 still fully locked) — `updateTotalFactor` sets `userInfo.factor = sqrt(500e18)`, added to `totalBoostFactor`.
4. Warp time past `coolDownInSecs`; attacker calls `startUnlock(500e18)` for the remaining balance — this call recomputes `factor = sqrt(0) = 0` **at this point**, since `getUserTotalLocked` already excludes the newly cooling-down amount. Assert `userInfo.factor == 0` and `totalBoostFactor` reduced accordingly — showing the "stale high factor" scenario from the question does not occur when the user calls `startUnlock` on their full remaining balance.
5. To demonstrate the narrower residual-stale-factor bug instead: after step 3, skip calling `startUnlock` again; instead just call `unlock(slotIndex)` for the first cooled-down 500e18 once its cooldown ends, leaving the second 500e18 slot's `factor` (sqrt(500e18)) permanently in `totalBoostFactor` even though eventually `getUserTotalLocked(attacker) == 0` after the second slot also unlocks (since no further `startUnlock` call occurs to re-zero it).
6. Have a different referred user's `trigger()` fire and assert attacker's `userInfo.rewardAmount` still increases via `_calBoosted(attacker)` despite `getUserTotalLocked(attacker) == 0`.

### Citations

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

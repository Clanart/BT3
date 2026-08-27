### Title
`forceUnLock` mutates cooldown slot state without settling vesting accrual, unlike `unlock` - ([File: VLMGP.sol])

### Summary
`unlock(uint256 _slotIndex)` calls `IMasterMagpie(masterMagpie).multiclaimFor(...)` to settle/harvest MGP rewards for the caller *before* zeroing `slot.amountInCoolDown` [1](#0-0) . `forceUnLock(uint256 _slotIndex)` never calls `multiclaimFor` or any equivalent harvest — it goes directly to `_unlock(slot.amountInCoolDown)` and `expectedPenaltyAmount(_slotIndex)`, then mutates `slot.amountInCoolDown` to `0` and `slot.endTime` to `block.timestamp` [2](#0-1) . Because `getRewardablePercentWAD` derives the rewardable share directly from `userUnlockings[user][i].amountInCoolDown` and `.endTime`/`.startTime` [3](#0-2) , wiping these fields without first harvesting leaves any un-harvested MGP accrual for that period permanently miscomputed the next time rewards are settled through `MasterMagpie`/`vlMGPBaseRewarder`.

### Finding Description
`unlock()` follows the sequence: check slot is out of cooldown → **harvest via `multiclaimFor`** (settling reward debt against the still-intact `slot.amountInCoolDown`/`endTime`) → then zero the slot and transfer MGP [1](#0-0) . This ordering guarantees that `getRewardablePercentWAD` (and the pool's `accMGPPerShare`/`rewardDebt` bookkeeping in `MasterMagpie._multiClaim`/`_harvestMGP`) is evaluated once against the pre-exit state before the slot data that feeds that computation is destroyed [4](#0-3) .

`forceUnLock()` skips this step entirely: it calls `_unlock(slot.amountInCoolDown)` and `expectedPenaltyAmount(_slotIndex)` (a `view` function only computing the penalty split, not settling rewards), then directly sets `slot.amountInCoolDown = 0` and `slot.endTime = block.timestamp` [2](#0-1) . No call into `masterMagpie` occurs to harvest/settle the MGP reward accrual tied to that cooldown period before the slot's `amountInCoolDown`/`endTime` — the exact fields `getRewardablePercentWAD` reads — are wiped.

The `updateTotalFactor` referral hook, called from `startUnlock` and gated by `if (userInfo.myCode == bytes32(0)) return;` in `ReferralStorage.sol` [5](#0-4) , only affects the referral boost factor (`totalBoostFactor`), not the vlMGP reward-debt/vesting settlement itself. It is not called by `unlock()` or `forceUnLock()` at all, so it cannot substitute for the missing harvest — confirming that for a caller with no registered referral code (the stated precondition), there is no alternate mechanism reconciling accrued yield before `forceUnLock` mutates the slot.

Since `forceUnLock` is a fully public, unprivileged-callable exit path (`external whenNotPaused nonReentrant`, gated only by `_checkInCoolDown`) [2](#0-1) , any user in cooldown can trigger this divergent, unsettled exit path instead of waiting for `unlock()`, permanently losing the reconciliation between `getRewardablePercentWAD` and the historical `amountInCoolDown`/`endTime` that `unlock()` would have preserved via a prior harvest.

### Impact Explanation
Because the reward-sharing computation in `getRewardablePercentWAD` is consumed downstream (referenced in `vlMGPBaseRewarder.sol`) to apportion MGP yield between fully-locked and cooling-down balances, silently zeroing `amountInCoolDown`/resetting `endTime` without a prior harvest desynchronizes the on-chain accrual state from the actual historical cooldown period. This causes MGP yield accrued during the cooldown window to be miscomputed/misattributed once a harvest eventually occurs — i.e., unclaimed yield that should have been settled under the pre-exit state is lost or diverted, matching the "Theft of unclaimed yield" (High) impact class.

### Likelihood Explanation
Any unprivileged EOA that has locked MGP, called `startUnlock`, waited through (or not) the cooldown, and never registered a referral code can call `forceUnLock(_slotIndex)` directly — no special role or capital beyond normal lock/cooldown participation is required, and the path is fully reachable through public functions with standard `nonReentrant`/`whenNotPaused` guards that do not prevent this state-mutation-without-settlement issue. This is repeatable for every cooldown slot and every user.

### Recommendation
Add the same settlement call performed in `unlock()` — `IMasterMagpie(masterMagpie).multiclaimFor(...)` for the vlMGP pool — at the start of `forceUnLock()`, before `_unlock()` mutates any lock/cooldown accounting and before `slot.amountInCoolDown`/`slot.endTime` are overwritten, so both exit paths settle vesting accrual under identical rules.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `VLMGP`, `MasterMagpie`, `ReferralStorage` per existing test fixtures; ensure user never calls `registerCode`/`useCode`.
2. User locks MGP via `lock()`, then calls `startUnlock(amount)` to create a cooldown slot.
3. Advance time to accrue MGP rewards on `MasterMagpie` for the vlMGP pool while the slot is in cooldown.
4. Table-test over `_slotIndex` boundaries (0, valid max index, and just past max to confirm `_checkIdexInBoundary` reverts) and over redemption timing relative to `endTime` (before `endTime`, exactly at `endTime`, well after `endTime`).
5. For each row: record `getRewardablePercentWAD(user)` and the pending MGP reward (`unClaimedMgp`/`_calNewMGP`) immediately before calling `forceUnLock(_slotIndex)`.
6. Call `forceUnLock(_slotIndex)`.
7. Assert: (a) no `multiclaimFor`/harvest event occurred on `MasterMagpie` for this user during the call; (b) `slot.amountInCoolDown == 0` and `slot.endTime == block.timestamp` immediately after, with no corresponding update to `unClaimedMgp`/`rewardDebt`; (c) `getRewardablePercentWAD(user)` computed afterward no longer reflects the pre-exit cooldown contribution, diverging from the value recorded in step 5 — demonstrating the accrual/state mismatch that `unlock()`'s prior `multiclaimFor` call would have prevented.
8. Repeat the same scenario using `unlock()` instead (after cooldown end) and assert the pre/post `getRewardablePercentWAD` and reward-debt values remain consistent, proving the two exit paths diverge.

### Citations

**File:** VLMGP.sol (L193-218)
```text
    function getRewardablePercentWAD(address _user) override public view returns(uint256 percent) {
        uint256 fullyInLock = getUserTotalLocked(_user);
        uint256 inCoolDown = getUserAmountInCoolDown(_user);
        uint256 userTotalVlmgp = fullyInLock + inCoolDown;
        if (userTotalVlmgp == 0)
            return 0;
        percent = fullyInLock * 1e18 / userTotalVlmgp;

        uint256 timeNow = block.timestamp;
        UserUnlocking[] storage userUnlocking = userUnlockings[_user];

        for (uint256 i; i < userUnlocking.length; i++) {
            if (userUnlocking[i].amountInCoolDown > 0) {
                if (block.timestamp > userUnlocking[i].endTime) {// fully unlocked 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 * (userUnlocking[i].endTime - userUnlocking[i].startTime)
                        / userTotalVlmgp / (timeNow - userUnlocking[i].startTime);
                }
                else {// still in cool down 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 / userTotalVlmgp;
                }

            }
        }

        return percent;
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

**File:** rewards/MasterMagpie.sol (L536-562)
```text
    function _multiClaim(address[] calldata _stakingTokens, address _user, address _receiver, address[][] memory _rewardTokens) internal nonReentrant {
        uint256 length = _stakingTokens.length;
        if (length != _rewardTokens.length) revert LengthMismatch();

        uint256 vlMGPPoolAmount;
        uint256 mWOmPoolAmount;
        uint256 defaultPoolAmount;

        for (uint256 i = 0; i < length; ++i) {
            address _stakingToken = _stakingTokens[i];
            UserInfo storage user = userInfo[_stakingToken][_user];
            
            updatePool(_stakingToken);
            uint256 claimableMgp = _calNewMGP(_stakingToken, _user) + unClaimedMgp[_stakingToken][_user];

            if (_stakingToken == address(vlmgp)) {
                vlMGPPoolAmount += claimableMgp;
            } else if (MPGRewardPool[_stakingToken]) {
                mWOmPoolAmount += claimableMgp;
            } else {
                defaultPoolAmount += claimableMgp;
            }

            unClaimedMgp[_stakingToken][_user] = 0;
            user.rewardDebt = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) / 1e12;
            _claimBaseRewarder(_stakingToken, _user, _receiver, _rewardTokens[i]);
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

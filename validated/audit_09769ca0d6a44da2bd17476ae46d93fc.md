Based on the code, this describes the intended vesting-decay mechanism rather than an exploitable flaw.

`getRewardablePercentWAD` computes: `percent = fullyInLock/total + Σ(cooldownSlots)`, where slots still cooling down (`block.timestamp <= endTime`) are counted at full weight (`amountInCoolDown * 1e18 / userTotalVlmgp`), and decay only kicks in for slots whose `endTime` has already passed and remain unclaimed (`percent += ... * (endTime-startTime)/(timeNow-startTime)`, which shrinks as `timeNow` grows past `endTime`). [1](#0-0) 

Calling `startUnlock` doesn't change `balanceOf(user)` (= `getUserTotalLocked + getUserAmountInCoolDown`), it only moves the amount from the "locked" bucket to the "cooling down" bucket, and `getRewardablePercentWAD` explicitly weights slots that are still cooling down (not yet past `endTime`) at 100%, i.e., no forfeit is due yet. [2](#0-1) [3](#0-2) 

`updateTotalFactor` in `ReferralStorage` is invoked synchronously inside `startUnlock`, and it recomputes `userInfo.factor = sqrt(getUserTotalLocked(account))` immediately after the lock/cooldown state changes, so `factor` stays reconciled with `getUserTotalLocked` at all times — there is no window where they diverge. [4](#0-3) [5](#0-4) 

The claim that "getRewardablePercentWAD only starts decaying after endTime has passed" is not a bug — it is the documented decay design: full reward weight while locked or while a cooldown slot is still pending, with decay applying only to slots that have matured (`endTime` passed) but were left unclaimed, to discourage users from sitting on withdrawable-but-unclaimed tokens. Reusing a zeroed slot (the `maxSlot` precondition) via `getNextAvailableUnlockSlot` just resets `startTime`/`endTime` for that slot index and does not create any double-counting or extra reward capture, since `_updateFor`/`_earned` accrue on `balanceOf` (locked + cooldown combined), which is unaffected by moving between those two buckets. [6](#0-5) [7](#0-6) 

No code path allows an unprivileged caller to extract more rewards than the design intends or to desynchronize `factor` from `getUserTotalLocked`.

### No vulnerability found for this question.

### Citations

**File:** VLMGP.sol (L113-120)
```text
    function balanceOf(address _user) public override view returns (uint256) {
        return getUserTotalLocked(_user) + getUserAmountInCoolDown(_user);
    }

    // total Mgp locked, excluding the ones in cool down
    function totalLocked() override public view returns (uint256) {
        return this.totalSupply() - this.totalAmountInCoolDown();
    }
```

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

**File:** VLMGP.sol (L220-232)
```text
    function getNextAvailableUnlockSlot(address _user) override public view returns (uint256) {
        uint256 length = getUserUnlockSlotLength(_user);
        if (length < maxSlot)
            return length;

        // length as maxSlot
        for (uint256 i; i < length; i++) {
            if (userUnlockings[_user][i].amountInCoolDown == 0)
                return  i;
        }

        revert AllUnlockSlotOccupied();
    }
```

**File:** VLMGP.sol (L289-308)
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
```

**File:** rewards/vlMGPBaseRewarder.sol (L349-361)
```text
    function _updateFor(address _account) internal {
        uint256 length = rewardTokens.length;
        uint256 userVlMGPAmount = balanceOf(_account);

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;

            userRewards[rewardToken][_account] = _earned(_account, rewardToken, userVlMGPAmount);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L386-400)
```text
    function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
        uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
        uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
        if (rewardableAmount > _amount)
            revert InvalidRewardableAmount();

        uint256 forfeitAmount = _amount - rewardableAmount;
        
        if (forfeitAmount < (_amount / 1000)) {  // if forfeitAmount is smaller than 0.1% ignore to save gas fee
            forfeitAmount = 0;
            rewardableAmount = _amount;
        }

        return forfeitAmount;
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

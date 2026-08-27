Confirmed: the two contracts are structurally identical except that `mWOMSVBaseRewarder._calExpireForfeit` never calls `mWOMSV.getRewardablePercentWAD(_account)` even though `mWomSV.sol` implements that exact function (mirroring `VLMGP.getRewardablePercentWAD`), which is the mechanism the sibling `vlMGPBaseRewarder._calExpireForfeit` uses to compute forfeiture for accounts with tokens in cooldown. This confirms the bug is real code, not speculation. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
mWOMSVBaseRewarder never forfeits/redistributes yield for cooling-down mWomSV lockers - ([File: rewards/mWOMSVBaseRewarder.sol])

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` sets `rewardableAmount = _amount` unconditionally instead of scaling by the account's rewardable percentage, so `forfeitAmount` is always `0`. The staking token `mWomSV` exposes `getRewardablePercentWAD(_account)` for exactly this purpose (mirroring `VLMGP.getRewardablePercentWAD` used by the sibling `vlMGPBaseRewarder`), but `mWOMSVBaseRewarder` never calls it.

### Finding Description
In `vlMGPBaseRewarder._calExpireForfeit`, the rewardable amount is computed as `_amount * vlMGP.getRewardablePercentWAD(_account) / 1e18`, so accounts with tokens still in cooldown forfeit a pro-rated share of their pending reward, and that forfeited share is routed back to the pool via `_queueNewRewardsWithoutTransfer` in `_sendReward` for the benefit of remaining fully-locked stakers [4](#0-3) .

`mWOMSVBaseRewarder._calExpireForfeit`, by contrast, sets `rewardableAmount = _amount` directly, ignoring the account's lock/cooldown state entirely, so `forfeitAmount = _amount - rewardableAmount` is always `0` [1](#0-0) . `mWomSV.sol` does implement `getRewardablePercentWAD(_user)`, computing a reduced percentage for cooling-down slots exactly like `VLMGP` [3](#0-2) , but `mWOMSVBaseRewarder` neither imports an interface exposing this getter (it uses the generic `ILocker` type) nor invokes it, meaning the pro-rated forfeiture logic present in the vlMGP counterpart was not carried over to the mWomSV rewarder.

An unprivileged account that has started `startUnlock` (cooling down part of their mWomSV, per `mWomSV.startUnlock`) can call `getReward` through MasterMagpie's multiclaim path — reaching `mWOMSVBaseRewarder.getReward` → `_sendReward` → `_calExpireForfeit` — and receive their full `userRewards[...]` amount with zero forfeiture, even though a portion of their balance is only in cooldown, not fully locked. No existing modifier (`onlyMasterMagpie`, `updateReward`) checks or corrects this, since the forfeiture computation itself is the defective logic.

### Impact Explanation
Because forfeiture never occurs, the `_queueNewRewardsWithoutTransfer` top-up path in `_sendReward` is unreachable for `mWOMSVBaseRewarder` (`forfeitAmount > 0` is never true) [5](#0-4) . Remaining fully-locked mWomSV stakers permanently lose the pro-rated yield top-up they would otherwise receive from cooling-down accounts' claims, which matches "theft or permanent freezing of unclaimed yield" for third-party lockers. This is a redistribution/accounting defect rather than a mechanism that lets the attacker drain the pool beyond their own earned share, but it does divert value away from other lockers relative to the intended (and code-present, in the vlMGP analog) design.

### Likelihood Explanation
No special capital or attacker sophistication is required: any holder with mWomSV in a cooldown slot (`startUnlock`) who calls `getReward`/multiclaim through MasterMagpie will hit this path automatically and repeatedly on every claim while any part of their balance is cooling down. There is no privileged role involved and no need for flash loans or reentrancy — the bug fires on the normal, expected claim flow.

### Recommendation
Update `mWOMSVBaseRewarder._calExpireForfeit` to mirror `vlMGPBaseRewarder._calExpireForfeit`: compute `rewardableAmount = _amount * mWOMSV.getRewardablePercentWAD(_account) / 1e18` (requiring `ILocker`/`mWomSV`'s interface to expose `getRewardablePercentWAD`), so cooling-down balances correctly forfeit a pro-rated share of pending rewards, which then flows into `_queueNewRewardsWithoutTransfer` for redistribution to remaining lockers.

### Proof of Concept
Hardhat test plan:
1. Deploy `mWomSV`, `mWOMSVBaseRewarder`, and `MasterMagpie` per existing test fixtures.
2. Fund two accounts, A and B, each locking equal mWomSV amounts.
3. Queue a reward amount via `queueNewRewards` and let rewards accrue (`rewardPerTokenStored` update).
4. Have account A call `mWomSV.startUnlock(partialAmount)` to move part of A's balance into cooldown (before `endTime`).
5. Call `getReward(A, A)` via MasterMagpie multiclaim path while A is still within the cooldown window.
6. Read `rewards[_rewardToken].queuedRewards` before and after step 5; also compute `mWomSV.getRewardablePercentWAD(A)` (expected `< 1e18`) to show a forfeit should have applied.
7. Assert: expected forfeit `= earnedByA * (1e18 - getRewardablePercentWAD(A)) / 1e18` is nonzero, but observed `queuedRewards` delta is `0` and A received the full `userRewards` amount — demonstrating the missing forfeiture/redistribution.

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L362-376)
```text
    function _sendReward(address _rewardToken, address _account, address _receiver) internal {
        uint256 forfeitAmount = _calExpireForfeit(_account, userRewards[_rewardToken][_account]);
        uint256 toSend = userRewards[_rewardToken][_account] - forfeitAmount;


        userRewards[_rewardToken][_account] = 0;
            
        if (toSend > 0) {
            IERC20(_rewardToken).safeTransfer(_receiver, toSend);
            emit RewardPaid(_account, _receiver, toSend, _rewardToken);
        }

        if(forfeitAmount > 0)
            _queueNewRewardsWithoutTransfer(forfeitAmount, _rewardToken);
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L385-398)
```text
    function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
        uint256 rewardableAmount = _amount;
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

**File:** rewards/vlMGPBaseRewarder.sol (L363-400)
```text
    function _sendReward(address _rewardToken, address _account, address _receiver) internal {
        uint256 forfeitAmount = _calExpireForfeit(_account, userRewards[_rewardToken][_account]);
        uint256 toSend = userRewards[_rewardToken][_account] - forfeitAmount;


        userRewards[_rewardToken][_account] = 0;
            
        if (toSend > 0) {
            IERC20(_rewardToken).safeTransfer(_receiver, toSend);
            emit RewardPaid(_account, _receiver, toSend, _rewardToken);
        }

        if(forfeitAmount > 0)
            _queueNewRewardsWithoutTransfer(forfeitAmount, _rewardToken);
    }

    function _earned(address _account, address _rewardToken, uint256 _userVlmgpShare) internal view returns (uint256) {
        return ((_userVlmgpShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**vlMGPDecimal) + userRewards[_rewardToken][_account];
    }

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

**File:** wombat/mWomSV.sol (L181-206)
```text
    function getRewardablePercentWAD(address _user) override public view returns(uint256 percent) {
        uint256 fullyInLock = getUserTotalLocked(_user);
        uint256 inCoolDown = getUserAmountInCoolDown(_user);
        uint256 userTotalmWomSV = fullyInLock + inCoolDown;
        if (userTotalmWomSV == 0)
            return 0;
        percent = fullyInLock * 1e18 / userTotalmWomSV;

        uint256 timeNow = block.timestamp;
        UserUnlocking[] storage userUnlocking = userUnlockings[_user];

        for (uint256 i; i < userUnlocking.length; i++) {
            if (userUnlocking[i].amountInCoolDown > 0) {
                if (block.timestamp > userUnlocking[i].endTime) {// fully unlocked 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 * (userUnlocking[i].endTime - userUnlocking[i].startTime)
                        / userTotalmWomSV / (timeNow - userUnlocking[i].startTime);
                }
                else {// still in cool down 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 / userTotalmWomSV;
                }

            }
        }

        return percent;
    }
```

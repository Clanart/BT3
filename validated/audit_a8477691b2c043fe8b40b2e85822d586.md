## Analysis

The reported bug class — a broken eligibility/restriction check that silently fails to enforce an intended condition — has a direct analog in `mWOMSVBaseRewarder.sol`'s reward forfeiture logic.

### Title
Broken forfeit calculation in `mWOMSVBaseRewarder._calExpireForfeit` lets users in cooldown claim full rewards, stealing yield owed to other stakers - (File: `rewards/mWOMSVBaseRewarder.sol`)

### Summary
`mWOMSVBaseRewarder` is meant to penalize users who are not fully locked in `mWomSV` (i.e., users who have started `startUnlock`/are in cooldown) by forfeiting a proportional share of their pending rewards back into the pool for remaining long-term lockers, exactly as its sibling contract `vlMGPBaseRewarder` does. However, the mWOMSV variant's `_calExpireForfeit` never queries the user's locked ratio, so the forfeit amount is always zero regardless of the user's actual lock status.

### Finding Description
Compare the two sibling implementations:

`vlMGPBaseRewarder.sol` correctly computes the forfeit using the user's rewardable percentage from the locker contract: [1](#0-0) 

`mWOMSVBaseRewarder.sol`'s equivalent function never calls `mWOMSV.getRewardablePercentWAD(_account)` at all — it just re-assigns `rewardableAmount = _amount`, making `forfeitAmount` always `0`: [2](#0-1) 

The `mWomSV` locker contract does implement a correct `getRewardablePercentWAD` function that computes a reduced percentage for users with tokens in cooldown or partially unlocked: [3](#0-2) 

But `mWOMSVBaseRewarder._calExpireForfeit` never invokes it, so this getter is dead code with respect to the forfeit mechanism. As a result, `_sendReward` always transfers 100% of `userRewards[_rewardToken][_account]` to the receiver and never routes anything to `_queueNewRewardsWithoutTransfer` for redistribution to remaining fully-locked stakers: [4](#0-3) 

### Impact Explanation
Any ordinary user who calls `startUnlock` on `mWomSV` (entering cooldown, no privileged role required) and then claims rewards via `MasterMagpie`/`getReward` receives their full, un-forfeited reward share instead of the reduced share the protocol design intends. The forfeited amount that should have been redistributed to users who kept their `mWomSV` fully locked (via `_queueNewRewardsWithoutTransfer`) never gets generated, so long-term lockers permanently lose the boosted yield they are owed. This is a direct redistribution/theft of unclaimed yield from one class of unprivileged users (fully-locked) to another (cooling-down/unlocking) — reachable purely through the normal user flow of locking, starting unlock, and claiming rewards.

### Likelihood Explanation
High likelihood: this requires no privileged role, no admin action, and no external protocol manipulation. Any holder of `mWomSV` who calls `startUnlock` (even for a trivial amount) and then triggers `getReward`/`getRewards` will bypass the forfeit entirely, every time, deterministically — it is not a race condition or edge case, but the ordinary, unconditional behavior of the function.

### Recommendation
Fix `_calExpireForfeit` in `rewards/mWOMSVBaseRewarder.sol` to mirror `vlMGPBaseRewarder`'s implementation, computing `rewardableAmount` from `mWOMSV.getRewardablePercentWAD(_account)` rather than assigning it equal to `_amount`:
```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardablePercentWAD = mWOMSV.getRewardablePercentWAD(_account);
    uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
    if (rewardableAmount > _amount)
        revert InvalidRewardableAmount();

    uint256 forfeitAmount = _amount - rewardableAmount;
    if (forfeitAmount < (_amount / 1000)) {
        forfeitAmount = 0;
        rewardableAmount = _amount;
    }
    return forfeitAmount;
}
```
Add unit tests that lock `mWomSV`, start a partial unlock, accrue rewards, and assert that `getReward` transfers less than 100% of pending rewards and that the difference is queued back into `rewardPerTokenStored` for other stakers.

### Proof of Concept
1. User A and User B each lock `1000 mWOM` into `mWomSV` (fully locked, `getRewardablePercentWAD` = 1e18 for both).
2. Rewards accrue and are queued via `queueNewRewards`, splitting evenly by `balanceOf` share in `MasterMagpie`.
3. User A calls `mWomSV.startUnlock(500)`, putting half of their balance into cooldown; per `getRewardablePercentWAD`, User A's rewardable percent should now be reduced (< 1e18) since `fullyInLock` decreased while `inCoolDown` increased.
4. User A calls `getReward` via `MasterMagpie` → `mWOMSVBaseRewarder.getReward`.
5. `_sendReward` invokes `_calExpireForfeit(_account, userRewards[...])`, which — due to the bug — returns `forfeitAmount == 0` regardless of the cooldown state computed in step 3.
6. User A receives 100% of accrued rewards despite having exited full-lock status, and no forfeited amount is queued back via `_queueNewRewardsWithoutTransfer` for User B, permanently denying User B the extra yield the protocol design intended to redirect.

### Citations

**File:** rewards/vlMGPBaseRewarder.sol (L386-391)
```text
    function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
        uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
        uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
        if (rewardableAmount > _amount)
            revert InvalidRewardableAmount();

```

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

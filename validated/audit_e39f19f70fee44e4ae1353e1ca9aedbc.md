### Title
mWOMSVBaseRewarder forfeit calculation is dead logic, allowing early/cooling-down mWomSV holders to claim full rewards - ([File: rewards/mWOMSVBaseRewarder.sol])

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` is meant to reduce a claiming user's reward according to how much of their `mWomSV` position is not "fully locked" (i.e. amounts in cooldown / unlocked), exactly like the analogous `vlMGPBaseRewarder._calExpireForfeit`, which calls `vlMGP.getRewardablePercentWAD(_account)`. In `mWOMSVBaseRewarder`, this call was omitted and `rewardableAmount` is simply set equal to `_amount`, so `forfeitAmount` is unconditionally `0` regardless of the user's actual lock status.

### Finding Description
`vlMGPBaseRewarder._calExpireForfeit` correctly computes the forfeitable portion of a reward based on the caller's rewardable percentage: [1](#0-0) 

`mWOMSVBaseRewarder._calExpireForfeit`, which is structurally the mWomSV analog of the same mechanism, never queries the equivalent `mWomSV.getRewardablePercentWAD(_account)` function (which exists and is fully implemented): [2](#0-1) [3](#0-2) 

Instead it sets `rewardableAmount = _amount` before the (dead) `rewardableAmount > _amount` check, guaranteeing `forfeitAmount = _amount - rewardableAmount = 0` on every call. This value feeds directly into `_sendReward`, which uses it to split the payout between the user (`toSend`) and the pool-wide forfeit re-queue (`_queueNewRewardsWithoutTransfer`): [4](#0-3) 

This mirrors the reported bug class exactly: a capping/penalty calculation that is supposed to select the lesser of two values (here, the "rewardable" fraction vs. the full amount) but structurally always resolves to the non-restrictive branch, so the safeguard never triggers.

### Impact Explanation
Any user who is not fully locked in `mWomSV` (i.e., has an amount in cooldown, or an expired/unlocked cooldown slot that should reduce their `getRewardablePercentWAD`) is entitled to only a partial share of accrued mWOMSV-pool rewards, with the remainder meant to be redistributed back into the reward pool for fully-locked, loyal stakers via `_queueNewRewardsWithoutTransfer`. Because `forfeitAmount` is always `0`, these users instead receive 100% of their accrued reward, and the redistribution pool that fully-locked stakers are entitled to is never funded with the forfeited share. This is a direct, permanent misallocation of unclaimed yield away from the long-term/fully-locked stakers who are supposed to receive it — every claim via `getReward`/`getRewards` on this rewarder is affected, and the loss to honest stakers compounds with each claim.

### Likelihood Explanation
This requires no privileged role and no special conditions: any ordinary wallet holding `mWomSV` who has ever started a cooldown (a normal, permitted user action via `mWomSV.startUnlock`) and then claims rewards through `MasterMagpie` (which invokes `getReward`/`getRewards` on this rewarder) triggers the flawed calculation on every single claim. This is a deterministic, always-on defect, not an edge case, and is trivially reachable during normal protocol usage.

### Recommendation
Mirror `vlMGPBaseRewarder._calExpireForfeit`: compute `rewardableAmount` from `mWOMSV.getRewardablePercentWAD(_account)` (i.e., `_amount * mWOMSV.getRewardablePercentWAD(_account) / 1e18`) instead of hardcoding `rewardableAmount = _amount`, so that `forfeitAmount` correctly reflects the user's non-fully-locked share and is properly re-queued to the reward pool.

### Proof of Concept
1. User locks `mWOM` into `mWomSV` and accrues rewards in `mWOMSVBaseRewarder`.
2. User calls `mWomSV.startUnlock(amount)` to move part of their balance into cooldown, which per `mWomSV.getRewardablePercentWAD` should reduce their rewardable percentage below 100%.
3. User calls `MasterMagpie`'s claim path, which calls `mWOMSVBaseRewarder.getReward`/`getRewards`, invoking `_sendReward` → `_calExpireForfeit(_account, userRewards[...])`. [4](#0-3) 
4. Because `_calExpireForfeit` sets `rewardableAmount = _amount` unconditionally, `forfeitAmount` computes to `0` regardless of the user's cooldown/unlocked status obtained from `mWomSV.getRewardablePercentWAD`. [2](#0-1) 
5. The user receives their entire reward (`toSend == userRewards[...]`) and no amount is re-queued to `rewardInfo.queuedRewards`/`rewardPerTokenStored` for redistribution to fully-locked stakers, permanently denying them the yield share they should have received.

### Citations

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

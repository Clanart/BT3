Confirmed: `mWomSV.sol` implements a fully-functional `getRewardablePercentWAD` function that computes a user's forfeitable share based on locked vs. cooldown amounts [1](#0-0) , exactly mirroring the pattern used by `VLMGP.getRewardablePercentWAD` which is correctly consumed by `vlMGPBaseRewarder._calExpireForfeit` [2](#0-1) . However, `mWOMSVBaseRewarder._calExpireForfeit` never calls `mWOMSV.getRewardablePercentWAD(_account)` at all — it sets `rewardableAmount = _amount` unconditionally, making the `forfeitAmount` always zero regardless of the user's actual lock/cooldown state [3](#0-2) .

### Title
mWOMSVBaseRewarder never applies early-unlock reward forfeiture, letting users always claim 100% of bonus rewards - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` is supposed to reduce a user's claimable bonus-reward amount proportionally to how much of their `mWomSV` position is still fully locked vs. in cooldown/unlocking, using `mWomSV.getRewardablePercentWAD`. Instead it computes `rewardableAmount = _amount` directly, bypassing the percentage calculation entirely, so `forfeitAmount` is always `0`.

### Finding Description
The forfeiture mechanism exists to discourage users from starting/using the unlock cooldown while still farming full bonus rewards from `mWomSV` — this mirrors the intended design in the sibling `vlMGPBaseRewarder`, which correctly multiplies the claim by `vlMGP.getRewardablePercentWAD(_account)` before computing `forfeitAmount = _amount - rewardableAmount` [4](#0-3) . The `mWomSV` contract fully implements the equivalent function, decreasing a user's rewardable percent for the portion of their balance sitting in a cooldown slot [1](#0-0) , but `mWOMSVBaseRewarder` never calls it [3](#0-2) . As a result, `_sendReward` always transfers the user's full `userRewards[...]` balance and the `_queueNewRewardsWithoutTransfer` forfeiture re-distribution path is dead code [5](#0-4) .

This is directly reachable by any ordinary `mWomSV` holder: a user can `startUnlock` on `mWomSV` (entering cooldown) and still call `getReward`/`getRewards` through `MasterMagpie`, collecting 100% of bonus rewards from `mWOMSVBaseRewarder` with no forfeiture, exactly as if they had kept their tokens fully locked.

### Impact Explanation
Users who initiate the unlock/cooldown of `mWomSV` (which is meant to reduce their reward-earning weight and be penalized) instead keep earning the full, undiminished reward stream. Rewards that should be forfeited and redistributed to still-locked stakers via `_queueNewRewardsWithoutTransfer` are permanently paid out to unlocking users instead — meaning honest fully-locked stakers permanently lose their expected reward share (funds diverted, not just frozen), matching the "theft or permanent freezing of unclaimed yield" impact criterion.

### Likelihood Explanation
High likelihood: this requires no privileged role, and any `mWomSV` holder benefits automatically and passively from the missing check on every `getReward`/`getRewards` call. No special conditions or racing is required — it is the default behavior of the contract.

### Recommendation
In `mWOMSVBaseRewarder._calExpireForfeit`, mirror `vlMGPBaseRewarder` and compute the rewardable amount from `mWomSV.getRewardablePercentWAD(_account)`:
```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardablePercentWAD = mWOMSV.getRewardablePercentWAD(_account);
    uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
    ...
}
```

### Proof of Concept
1. A user locks `mWOM` into `mWomSV`, accruing bonus rewards through `mWOMSVBaseRewarder`.
2. The user calls `mWomSV.startUnlock(fullAmount)`, moving their entire balance into cooldown — under the intended design (as implemented in `mWomSV.getRewardablePercentWAD`) this should sharply reduce their rewardable percentage.
3. The user calls `getReward`/`getRewards` via `MasterMagpie`; `_sendReward` invokes `_calExpireForfeit`, which — due to the bug — returns `forfeitAmount = 0` regardless of the cooldown state [5](#0-4) .
4. The user receives 100% of accrued rewards with no penalty, while still-locked stakers never receive the redistributed forfeiture they were entitled to.

### Citations

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

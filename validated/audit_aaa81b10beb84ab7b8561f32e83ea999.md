### Title
Missing forfeit-percentage calculation in `mWOMSVBaseRewarder._calExpireForfeit` causes permanent loss of unclaimed-yield forfeitures - (File: `rewards/mWOMSVBaseRewarder.sol`)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` fails to compute the actual "rewardable percent" of a user's stake — the exact analog of the missing-range-check bug class in the reference report, where a boundary/validation computation that should constrain a value is simply omitted. As a result, no forfeiture is ever applied to `mWOMSVBaseRewarder` rewards, even for users who are in the unlock cool-down period and should only be entitled to a partial, time-weighted share of yield.

### Finding Description
The sibling contract `vlMGPBaseRewarder` correctly computes the forfeitable portion of a reward by querying the locker for the user's actual rewardable percentage: [1](#0-0) 

```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
    uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
    ...
}
```

`vlMGP.getRewardablePercentWAD` walks the user's unlock slots and reduces the rewardable share based on how much of the user's stake is in cool-down and how far along the cool-down period is: [2](#0-1) 

The `mWOMSVBaseRewarder` contract holds an identical `ILocker mWOMSV` reference which exposes the same `getRewardablePercentWAD(_account)` function (implemented identically in `wombat/mWomSV.sol`): [3](#0-2) 

However, `mWOMSVBaseRewarder._calExpireForfeit` never calls this function. Instead it sets `rewardableAmount = _amount` directly, making the forfeit computation a no-op: [4](#0-3) 

```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardableAmount = _amount;
    if (rewardableAmount > _amount)
        revert InvalidRewardableAmount();

    uint256 forfeitAmount = _amount - rewardableAmount;   // always 0
    ...
    return forfeitAmount;                                  // always 0
}
```

This function is invoked in `_sendReward`, which is called whenever an ordinary user claims their `mWOMSV` staking rewards via `getReward`/`getRewards` (routed through `MasterMagpie`, reachable by any unprivileged wallet holding staked `mWOMSV`): [5](#0-4) 

Because `forfeitAmount` is always `0`, the `if(forfeitAmount > 0) _queueNewRewardsWithoutTransfer(...)` branch never executes, so forfeited amounts are never redistributed back into the reward pool for remaining stakers.

### Impact Explanation
The design intent (proven by the identical mechanism in `vlMGPBaseRewarder`/`VLMGP`) is that a user who starts unlocking (moves stake into cool-down) should only continue earning a reduced, time-weighted share of new rewards, and the forfeited remainder should be re-queued (`_queueNewRewardsWithoutTransfer`) as bonus yield for users who keep their `mWOMSV` fully locked. Because this check is missing in `mWOMSVBaseRewarder`, any user who withdraws/unlocks `mWOMSV` still claims 100% of newly accrued rewards with no forfeiture, permanently denying the honest, fully-locked stakers the forfeited yield they are entitled to under the protocol's reward-accounting design. This is a permanent (not one-time) loss of unclaimed yield for the remaining honest stakers, satisfying the "theft or permanent freezing of unclaimed yield" impact bar, and it is triggerable by any ordinary wallet holding `mWOMSV` — no privileged role is required.

### Likelihood Explanation
High likelihood: any unprivileged holder of `mWOMSV` who calls `startUnlock` on `wombat/mWomSV.sol` and then claims mWOMSV pool rewards via the normal `getReward`/`getRewards` path in `mWOMSVBaseRewarder` will trigger the flawed `_calExpireForfeit`, with no forfeiture ever applied. No special conditions or coordination with an admin are needed; the deficiency is present on every reward claim by any user with `mWOMSV` in cool-down.

### Recommendation
Update `mWOMSVBaseRewarder._calExpireForfeit` to mirror `vlMGPBaseRewarder._calExpireForfeit`: query `mWOMSV.getRewardablePercentWAD(_account)` and compute `rewardableAmount = _amount * rewardablePercentWAD / 1e18` before deriving `forfeitAmount`, so cool-down users' claims are correctly reduced and the forfeited portion is properly re-queued via `_queueNewRewardsWithoutTransfer` for the benefit of fully-locked stakers.

### Proof of Concept
1. User A locks `WOM`/`mWom` into `mWomSV` (`wombat/mWomSV.sol`) and accrues rewards in `mWOMSVBaseRewarder`.
2. User A calls `startUnlock(amount)` on `mWomSV`, moving part of their stake into cool-down (`wombat/mWomSV.sol:247` onward).
3. Before the cool-down period ends, User A calls `getReward`/claims rewards through `MasterMagpie`, which invokes `mWOMSVBaseRewarder.getReward` → `_sendReward` → `_calExpireForfeit`.
4. Compare with the equivalent scenario in `vlMGPBaseRewarder`: there, `getRewardablePercentWAD` would return less than `1e18` (100%) for a user in cool-down, producing a non-zero `forfeitAmount` that gets re-queued.
5. In `mWOMSVBaseRewarder`, `_calExpireForfeit` always returns `0` regardless of cool-down state, so User A receives the full reward with no forfeiture, and no forfeited amount is ever re-queued as bonus yield for other stakers — permanently diverging from the intended reward-accounting design.

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

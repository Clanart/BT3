### Title
`mWOMSVBaseRewarder._calExpireForfeit` never applies the intended forfeit, letting users always receive the full reward that should sometimes be partially forfeited - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder.sol` is meant to mirror `vlMGPBaseRewarder.sol`'s early-exit reward forfeiture logic, penalizing mWomSV holders who are not fully "locked" (e.g. holders with tokens in cooldown/unlocking) by forfeiting part of their pending reward. In `vlMGPBaseRewarder`, `_calExpireForfeit` correctly computes `rewardableAmount` from `vlMGP.getRewardablePercentWAD(_account)`, so `forfeitAmount = _amount - rewardableAmount` can be non-zero. In `mWOMSVBaseRewarder`, the equivalent function hardcodes `rewardableAmount = _amount`, making the guard `if (rewardableAmount > _amount) revert` a tautological no-op and `forfeitAmount = _amount - rewardableAmount` always `0`.

### Finding Description
Compare the two sibling implementations:

`vlMGPBaseRewarder.sol` correctly derives the rewardable share from the locker contract: [1](#0-0) 

`mWOMSVBaseRewarder.sol` instead sets `rewardableAmount` equal to the full `_amount`, so the subtraction can never yield a forfeit: [2](#0-1) 

This function is invoked from `_sendReward`, which is the path used both for normal `getReward`/`getRewards` claims and for the `queueMGP`-equivalent distribution flow, exactly analogous to `vlMGPBaseRewarder._sendReward`: [3](#0-2) 

`mWomSV.sol` itself exposes `getRewardablePercentWAD(address)`, computed the same way as `VLMGP.sol`'s version, which is clearly intended to feed into the forfeit calculation but is never referenced by `mWOMSVBaseRewarder._calExpireForfeit`: [4](#0-3) 

Because the forfeit function is dead/broken, `_queueNewRewardsWithoutTransfer(forfeitAmount, _rewardToken)` in `_sendReward` is effectively unreachable with a non-zero amount, so forfeited rewards that should be redistributed to fully-locked mWomSV holders are instead paid out in full to any account, including those that have started unlocking (i.e., partially exited the lock).

### Impact Explanation
Users who start an unlock (`mWomSV.startUnlock`) but have not fully vested/relocked still receive 100% of pending reward tokens through `mWOMSVBaseRewarder`, instead of the reduced, prorated amount the protocol design intends (as implemented correctly in the vlMGP equivalent). This causes permanent misallocation/leakage of yield that should have been forfeited and redistributed (via `_queueNewRewardsWithoutTransfer`) to remaining, fully-locked stakers — a direct theft of unclaimed yield from honest long-term lockers who never receive the redistributed forfeit amounts.

### Likelihood Explanation
This is triggered by any unprivileged mWomSV holder calling `startUnlock` and then claiming rewards through the normal `getReward`/`getRewards` flow — no special permissions or governance actions are required. It happens on every reward claim by every non-fully-locked account, so the impact is deterministic and continuous, not a one-off edge case.

### Recommendation
Update `mWOMSVBaseRewarder._calExpireForfeit` to compute `rewardableAmount` from `mWOMSV.getRewardablePercentWAD(_account)`, mirroring `vlMGPBaseRewarder._calExpireForfeit`:
```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardablePercentWAD = mWOMSV.getRewardablePercentWAD(_account);
    uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
    ...
}
```

### Proof of Concept
1. User locks mWOM into `mWomSV` via `lock`/`lockFor`.
2. Protocol accrues rewards for the user through `mWOMSVBaseRewarder.queueNewRewards`/`_provisionReward`, incrementing `rewardPerTokenStored`.
3. User calls `mWomSV.startUnlock(amount)`, moving part of their balance into cooldown (`getRewardablePercentWAD` for this user would now be < 1e18 in the intended design).
4. User calls `getReward`/`getRewards` on `mWOMSVBaseRewarder`; `_sendReward` calls `_calExpireForfeit(_account, userRewards[...])`, which returns `0` regardless of the user's cooldown state due to the hardcoded `rewardableAmount = _amount`.
5. User receives the entire pending reward with no forfeiture, while the intended forfeited share is never captured into `_queueNewRewardsWithoutTransfer` for redistribution to still-fully-locked holders.

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

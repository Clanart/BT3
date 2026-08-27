Confirmed: `mWomSV.sol` implements `getRewardablePercentWAD` (mirroring `VLMGP.sol`), but `mWOMSVBaseRewarder.sol::_calExpireForfeit` never calls it, unlike the analogous `vlMGPBaseRewarder.sol::_calExpireForfeit` which correctly does `rewardableAmount = _amount * vlMGP.getRewardablePercentWAD(_account) / 1e18`.

### Title
mWOMSVBaseRewarder never applies the unlock-based reward forfeit ratio, permanently denying honest stakers their expected forfeited-yield redistribution - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder::_calExpireForfeit` is supposed to compute how much of a user's pending reward must be forfeited (and redistributed to remaining stakers) based on how much of their `mWomSV` position is still locked versus unlocking. The sibling contract `vlMGPBaseRewarder` implements this correctly by consulting the locker's `getRewardablePercentWAD`, but `mWOMSVBaseRewarder` does not, causing the forfeit ratio to always evaluate to zero regardless of the ratio's intended value.

### Finding Description
`ILocker`/`mWomSV.sol` exposes `getRewardablePercentWAD(_user)`, which computes the fraction (in WAD) of a user's rewards that should remain claimable, penalizing users who have started an unlock/cool-down. This is exactly what `vlMGPBaseRewarder::_calExpireForfeit` uses: [1](#0-0) 

In contrast, `mWOMSVBaseRewarder::_calExpireForfeit` sets `rewardableAmount = _amount` unconditionally and never queries `mWOMSV.getRewardablePercentWAD(_account)`, so `forfeitAmount = _amount - rewardableAmount` is always `0`: [2](#0-1) 

This function is invoked from `_sendReward`, which normally routes the forfeited portion back into the reward pool via `_queueNewRewardsWithoutTransfer` (increasing `rewardPerTokenStored` for remaining stakers): [3](#0-2) 

Because `forfeitAmount` is always `0`, this redistribution path is dead code — users who begin unlocking `mWomSV` (entering cool-down, per `mWomSV.sol`'s `startUnlock`/`UserUnlocking` mechanics) still receive 100% of their pending rewards with no penalty, and no forfeited yield is ever generated for stakers who keep their position locked.

### Impact Explanation
This breaks the documented/intended incentive design mirrored from `VLMGP`: users are supposed to be economically discouraged from prematurely unlocking by forfeiting a portion of unclaimed rewards, with that forfeited amount flowing to loyal, still-locked stakers. Since the check is bypassed, unlocking users permanently keep yield that honest, still-locked stakers were entitled to receive via redistribution. This is a permanent loss of unclaimed yield for the remaining `mWomSV` reward-pool participants, reachable by any ordinary unprivileged wallet simply by starting an unlock and then calling `getReward`/`getRewards`.

### Likelihood Explanation
High likelihood — this triggers on the normal, unprivileged user flow of unlocking `mWomSV` and claiming rewards through `mWOMSVBaseRewarder::getReward` / `getRewards`, requiring no special permissions, price manipulation, or governance action. It occurs every single time any user claims rewards while in cool-down.

### Recommendation
Update `mWOMSVBaseRewarder::_calExpireForfeit` to mirror `vlMGPBaseRewarder`'s logic by fetching `mWOMSV.getRewardablePercentWAD(_account)` and computing `rewardableAmount = _amount * rewardablePercentWAD / 1e18` before deriving `forfeitAmount`.

### Proof of Concept
1. A user locks `mWOM` into `mWomSV` and accrues pending rewards in `mWOMSVBaseRewarder`.
2. The user calls `startUnlock` (per `mWomSV.sol`) to begin a cool-down for their full/partial position, which should reduce their `getRewardablePercentWAD` well below `1e18`.
3. The user calls `getReward`/`getRewards` on `mWOMSVBaseRewarder`, triggering `_sendReward` → `_calExpireForfeit(_account, userRewards[...])`.
4. Because `_calExpireForfeit` never reads `getRewardablePercentWAD`, `rewardableAmount` is hardcoded to `_amount` and `forfeitAmount` is always `0` — the user receives their entire pending reward with no penalty, and `_queueNewRewardsWithoutTransfer` is never invoked, so remaining locked stakers never receive the redistributed forfeit that the protocol design (as evidenced by the identical, correctly-implemented mechanism in `vlMGPBaseRewarder`) intends for them.

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

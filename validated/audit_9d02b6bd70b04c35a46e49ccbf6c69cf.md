### Title
`mWOMSVBaseRewarder._calExpireForfeit` never applies the reward forfeit percentage, letting users in cooldown claim full rewards - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` is supposed to reduce a user's claimable reward amount by the percentage that is not "rewardable" (i.e., the portion of their mWomSV position that is in an unlock cooldown and thus should forfeit yield), mirroring the sibling implementation in `vlMGPBaseRewarder`. Instead, it sets `rewardableAmount = _amount` unconditionally and never consults `mWOMSV.getRewardablePercentWAD(_account)`, so `forfeitAmount` is always `0`.

### Finding Description
`mWomSV` tracks, per user, the percentage of their locked balance that is still fully "in-lock" vs. in an unlock cooldown slot via `getRewardablePercentWAD` ( [1](#0-0) ). The intended design (implemented correctly in the analogous `vlMGPBaseRewarder`) is for the reward pool to fetch this percentage and only pay out that pro-rated share of accrued rewards, forfeiting the rest back into the pool for other stakers: [2](#0-1) 

`mWOMSVBaseRewarder._calExpireForfeit`, however, is missing the call to `mWOMSV.getRewardablePercentWAD(_account)` entirely and just assigns `rewardableAmount = _amount`, making `forfeitAmount = _amount - rewardableAmount` always equal to `0`: [3](#0-2) 

This function is invoked on every reward claim path — `_sendReward`, which is called from `getReward`/`getRewards` (reachable by any ordinary user through `MasterMagpie`'s claim flow) — as well as from the public view helper `calExpireForfeit`: [4](#0-3) [5](#0-4) 

Because `forfeitAmount` is always zero, the intended forfeit-and-redistribute mechanism (`_queueNewRewardsWithoutTransfer`, which would otherwise return the forfeited amount to `rewardPerTokenStored` for other stakers) never triggers, and any user who starts an unlock/cooldown on their mWomSV position still receives 100% of their accrued mWOM-pool rewards instead of the reduced, cooldown-adjusted amount.

### Impact Explanation
This is a direct analog of the reported bug class: a value/entitlement calculation fails to apply the correct proportional factor (here, the "rewardable percent" tied to the user's cooldown state), resulting in systematic overpayment to users at the expense of the reward pool and other legitimate stakers. Every mWomSV holder who initiates `startUnlock` (a normal, unprivileged action) permanently avoids the yield forfeiture the protocol design intends to apply, and that forfeited value — which should be redistributed to other stakers — is instead diverted directly to the unlocking user. This is a concrete, protocol-wide, permanent misallocation/loss of reward funds triggered purely by ordinary user transactions.

### Likelihood Explanation
High likelihood: the bug is deterministic (no race condition or privileged action required) and triggers on the standard `startUnlock` → `getReward`/`getRewards` flow that any mWomSV staker would use.

### Recommendation
Update `mWOMSVBaseRewarder._calExpireForfeit` to mirror `vlMGPBaseRewarder._calExpireForfeit`, i.e., fetch `mWOMSV.getRewardablePercentWAD(_account)` and compute `rewardableAmount = _amount * rewardablePercentWAD / 1e18` before deriving `forfeitAmount`.

### Proof of Concept
1. User locks mWOM into `mWomSV`, accruing rewards over time via `mWOMSVBaseRewarder`.
2. User calls `mWomSV.startUnlock(amount)`, placing a portion of their balance into cooldown; per design this portion should no longer be "rewardable" per `getRewardablePercentWAD`.
3. User calls `MasterMagpie`'s claim function, which routes to `mWOMSVBaseRewarder.getReward`/`getRewards` → `_sendReward` → `_calExpireForfeit`.
4. Because `_calExpireForfeit` never reads `getRewardablePercentWAD`, `forfeitAmount` returns `0` regardless of how much of the user's balance is in cooldown, and the user receives the full `userRewards[...]` amount instead of the reduced, forfeit-adjusted amount that other stakers were meant to receive via `_queueNewRewardsWithoutTransfer`.

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

**File:** rewards/mWOMSVBaseRewarder.sol (L188-190)
```text
    function calExpireForfeit(address _account, address _rewardToken) public view returns(uint256) {
        return _calExpireForfeit(_account, earned(_account, _rewardToken));
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

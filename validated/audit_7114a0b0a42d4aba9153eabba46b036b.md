### Title
Forfeiture mechanism in `mWOMSVBaseRewarder` is permanently disabled, letting cooldown-cycling stakers keep rewards intended to be redistributed to fully-locked stakers - ([File: rewards/mWOMSVBaseRewarder.sol])

### Summary
`_calExpireForfeit` in `mWOMSVBaseRewarder.sol` never forfeits any reward because it compares `rewardableAmount` to itself instead of computing an actual reward-eligible percentage (unlike the sibling `vlMGPBaseRewarder`, which calls `vlMGP.getRewardablePercentWAD(_account)`). As a result, an account can repeatedly cycle through `startUnlock`/cooldown on `mWomSV` and always claim 100% of its accrued reward share, none of which is ever redirected back into the pool for fully-locked stakers.

### Finding Description
`_calExpireForfeit` in `rewards/mWOMSVBaseRewarder.sol`: [1](#0-0) 
sets `rewardableAmount = _amount` and then checks `if (rewardableAmount > _amount) revert(...)`, which is always false, so `forfeitAmount = _amount - rewardableAmount` is always `0`. This function is called from `_sendReward` on every `getReward`/`getRewards` call: [2](#0-1) 

Compare this to the analogous `vlMGPBaseRewarder._calExpireForfeit`, which correctly queries the locker's cooldown-weighted eligibility via `vlMGP.getRewardablePercentWAD(_account)` before computing `rewardableAmount`: [3](#0-2) 

The counterpart locker `mWomSV.sol` actually implements the equivalent function, `getRewardablePercentWAD`, which reduces an account's eligible percentage while its tokens sit in cooldown slots: [4](#0-3) 

However, `mWOMSVBaseRewarder` never calls `mWOMSV.getRewardablePercentWAD(...)` anywhere in the contract — `_calExpireForfeit` is dead/broken code that always returns `0` forfeiture.

Reward accrual itself (`_earned`, `_updateFor`) is a standard proportional-index accounting scheme and is not, by itself, exploitable — the `userRewardPerTokenPaid != rewardPerToken` skip in `_updateFor`/`updateRewards` is merely a gas-saving no-op guard and does not affect correctness. The actual defect is that the forfeiture haircut, which the protocol's design (as demonstrated by the vlMGP twin contract) clearly intends to apply to cooldown/unlocking balances, is never applied here. Any account can therefore repeatedly: lock mWOM → `startUnlock` a slot → wait/claim/queue new rewards → `unlock` → relock, and at every `getReward` call it keeps 100% of its `_earned` share computed from `balanceOf(account)` (which itself includes the full cooldown-portion balance, per `mWomSV.balanceOf`). None of that "should-be-forfeited" portion is ever redirected via `_queueNewRewardsWithoutTransfer` to the fully-locked stakers, unlike in the vlMGP flow.

### Impact Explanation
This is a loss of unclaimed yield to long-term/fully-locked stakers: the reward pool's total distributed rewards remain the same, but the redistribution mechanism that should shift a forfeited portion from cooldown/unlocking accounts back into the pool for fully-locked accounts never fires. Cooldown-cycling accounts permanently keep reward shares that the protocol's own design (mirrored 1:1 in `vlMGPBaseRewarder`) intends to claw back, at the expense of stakers who keep their tokens fully locked. This matches the "theft/permanent loss of unclaimed yield" impact class, scoped specifically to the differential between what a cooldown-cycling account should forfeit and what it actually keeps.

### Likelihood Explanation
This requires no special privilege — any holder of `mWomSV` can call `startUnlock`/`unlock` on their own balance and call `getReward` through `MasterMagpie` as normal user flow. It is fully repeatable across arbitrarily many epochs/slots and needs no flash loans, front-running, or governance access; it is a straightforward, always-triggerable defect of the deployed logic itself (broken self-comparison), not a rare edge case.

### Recommendation
Fix `_calExpireForfeit` in `mWOMSVBaseRewarder.sol` to actually compute the reward-eligible percentage from the locker, mirroring `vlMGPBaseRewarder`:
```solidity
uint256 rewardablePercentWAD = mWOMSV.getRewardablePercentWAD(_account);
uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
```
Add `getRewardablePercentWAD` to the `ILocker` interface used by `mWOMSVBaseRewarder` if not already exposed, and add regression tests asserting that partial-cooldown/cycling accounts forfeit a nonzero share back into `rewardPerTokenStored` via `_queueNewRewardsWithoutTransfer`.

### Proof of Concept
Hardhat plan:
1. Deploy `mWomSV`, `MasterMagpie`, `mWOMSVBaseRewarder`, and a reward token.
2. Have Attacker lock a fixed amount of mWOM, and Honest staker lock the same amount and never unlock.
3. Loop over N epochs: manager calls `queueNewRewards`; Attacker calls `startUnlock(partial)` then, once cooldown ends, `unlock(slot)` and relocks immediately, calling `getReward` each epoch through `MasterMagpie`.
4. Assert `rewards[token].queuedRewards`/`rewardPerTokenStored` never increases via `ForfeitRewardAdded` events (confirming `_calExpireForfeit` always returns 0), and compare Attacker's cumulative claimed reward to the theoretical forfeiture-adjusted entitlement computed using `mWomSV.getRewardablePercentWAD(attacker)` at each cooldown snapshot (mirroring the vlMGP logic) — expect Attacker's actual claim to exceed this theoretical entitlement by the full forfeited amount every cycle, while Honest staker's cumulative claim is correspondingly short of what redistribution should have given it.

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

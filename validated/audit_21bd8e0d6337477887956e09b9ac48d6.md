### Title
`_calExpireForfeit` in mWOMSVBaseRewarder never forfeits expired unclaimed yield, permanently diverting rewards that should return to other mWomSV lockers - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` sets `rewardableAmount = _amount` unconditionally instead of computing it from `mWomSV.getRewardablePercentWAD(_account)`, so `forfeitAmount` is always `0`. This means a user sitting in an expired-but-unclaimed cool-down/unlock slot (`block.timestamp > endTime`, never calling `unlock()`) keeps accruing and can fully claim 100% of `userRewards` via `getReward -> _sendReward -> _calExpireForfeit`, instead of having a decayed portion redirected to remaining lockers as `mWomSV.getRewardablePercentWAD` is designed to enforce.

### Finding Description
`_calExpireForfeit` is defined as: [1](#0-0) 

Note `rewardableAmount` is initialized to `_amount` and never reassigned from `mWOMSV.getRewardablePercentWAD(_account)` (unlike what the naming/design intent and the `mWomSV.getRewardablePercentWAD` decay formula suggest). Consequently `forfeitAmount = _amount - rewardableAmount` is always `0`, and the `if (rewardableAmount > _amount) revert` check is dead code since they're always equal.

`getRewardablePercentWAD` in `mWomSV.sol` implements a documented decay: percent contribution from cool-down amounts scales by `(endTime - startTime) / (timeNow - startTime)` once a slot is past `endTime`, i.e., value decays the longer a user leaves an expired slot unclaimed: [2](#0-1) 

This function exists specifically to compute a reduced "rewardable" percentage for accounts with expired unclaimed unlock slots. In `mWOMSVBaseRewarder`, `_sendReward` calls `_calExpireForfeit` expecting it to compute the forfeit based on this decay: [3](#0-2) 

Because `_calExpireForfeit` never calls `getRewardablePercentWAD`, `forfeitAmount` is always `0`, `toSend` always equals the full `userRewards[_rewardToken][_account]`, and `_queueNewRewardsWithoutTransfer` (which would redistribute forfeited amounts to remaining stakers) is never invoked with a nonzero amount.

Attack path: an attacker (or any regular user) holds `mWomSV`, calls `startUnlock` to place tokens into a cool-down slot, lets `endTime` pass without ever calling `unlock()`, and continues accruing `rewardPerToken`-based yield on the still-counted balance (since `balanceOf` in the rewarder reads `stakingInfo` from `MasterMagpie`, which still counts the unclaimed cool-down balance). They then call `getReward` (via `MasterMagpie`) which routes to `_sendReward -> _calExpireForfeit`, always receiving `forfeitAmount = 0` and thus 100% of accrued rewards, when by design a decayed/forfeited portion should have been retained and redistributed via `_queueNewRewardsWithoutTransfer` to other lockers.

Existing checks (`onlyMasterMagpie`, `updateReward`, `nonReentrant` on `getRewards`) do not prevent this — they gate access control and reentrancy, not the forfeiture calculation logic itself, which is simply a no-op.

### Impact Explanation
This is not "theft of principal" in the sense of draining another user's `userRewards` mapping entry directly — each user only claims their own already-accrued `userRewards[_rewardToken][_account]`, computed via the standard reward-per-token accounting. The bug means the *forfeiture/decay mechanism* that is supposed to claw back a portion of yield from stale, expired, unclaimed unlock slots and redistribute it to active lockers (`_queueNewRewardsWithoutTransfer`) never triggers. Under the documented design (mirrored by `getRewardablePercentWAD`'s decay formula and the presence of `ForfeitRewardAdded` event/`_queueNewRewardsWithoutTransfer` mechanism), users who leave slots expired-but-unclaimed should lose an increasing share of rewardability, and that lost share should flow to remaining lockers. Since this never happens, remaining lockers are permanently deprived of yield that should have been redistributed to them — this matches "theft or permanent freezing of unclaimed yield" from the perspective of the intended beneficiaries (active lockers), since that yield is instead paid out in full to accounts that should have forfeited part of it.

### Likelihood Explanation
- No special privilege is needed: any mWomSV holder can trigger `startUnlock`, wait past `endTime`, and simply avoid calling `unlock()`.
- The condition is reachable through the standard user-facing flow: `startUnlock -> getReward (via MasterMagpie) -> _sendReward -> _calExpireForfeit`.
- This is 100% reproducible every time rewards are claimed while any slot is expired-but-unclaimed, for any account, with no capital requirements beyond normal staking.
- The behavior is deterministic and always occurs (`forfeitAmount` is unconditionally 0), so likelihood is high given the mechanism appears purely broken rather than exploitable only under narrow conditions.

### Recommendation
Fix `_calExpireForfeit` to actually apply the decay computed by `mWOMSV.getRewardablePercentWAD(_account)`:
```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardablePercentWAD = mWOMSV.getRewardablePercentWAD(_account);
    uint256 rewardableAmount = (_amount * rewardablePercentWAD) / 1e18;
    if (rewardableAmount > _amount)
        revert InvalidRewardableAmount();

    uint256 forfeitAmount = _amount - rewardableAmount;

    if (forfeitAmount < (_amount / 1000)) {
        forfeitAmount = 0;
    }

    return forfeitAmount;
}
```
Also confirm `ILocker` interface exposes `getRewardablePercentWAD` so `mWOMSV.getRewardablePercentWAD(_account)` compiles (it is declared on `ILocker` per `mWomSV.sol` override signature).

### Proof of Concept
Foundry test plan:
1. Deploy `mWomSV`, `MasterMagpie`, `mWOMSVBaseRewarder` with a reward token; set `coolDownInSecs` to a small value (e.g., 1 day).
2. User A locks `1000 mWOM` via `mWomSV.lock`, staking reflected in `MasterMagpie`.
3. Manager calls `queueNewRewards` on the rewarder to donate reward tokens, accruing `rewardPerToken`.
4. User A calls `startUnlock(1000)` to place entire balance into a cool-down slot.
5. Warp time forward well past `endTime` (e.g., `endTime + 30 days`) without calling `unlock()`.
6. Assert `mWomSV.getRewardablePercentWAD(userA)` returns a value significantly less than `1e18` (reflecting decay), e.g., via direct call.
7. Call `getReward` via `MasterMagpie` for User A, capturing `RewardPaid` event and `_calExpireForfeit` return via `calExpireForfeit(userA, rewardToken)` — assert it returns `0` despite `getRewardablePercentWAD` indicating decayed rewardability.
8. Assert `toSend == userRewards[_rewardToken][userA]` (full amount, no forfeiture) and `historicalRewards`/`queuedRewards` show no `ForfeitRewardAdded` event was ever emitted, confirming the decay/forfeiture mechanism is dead code and 100% of stale, expired-slot-based rewards leak to the claimant instead of being redistributed to active lockers.

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

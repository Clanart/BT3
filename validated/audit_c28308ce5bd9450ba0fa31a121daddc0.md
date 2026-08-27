This confirms the vulnerability. `vlMGPBaseRewarder._calExpireForfeit` correctly calls `vlMGP.getRewardablePercentWAD(_account)` to scale down `rewardableAmount`, but `mWOMSVBaseRewarder._calExpireForfeit` never calls `mWOMSV.getRewardablePercentWAD(_account)` — it just sets `rewardableAmount = _amount`, making `forfeitAmount` always `0` regardless of lock status. [1](#0-0) [2](#0-1) 

### Title
Broken forfeiture logic in `_calExpireForfeit` allows full reward theft on unlock - ([File: rewards/mWOMSVBaseRewarder.sol])

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` fails to consult `mWomSV.getRewardablePercentWAD(_account)` as its sibling contract `vlMGPBaseRewarder` does, so `rewardableAmount` is always set equal to the full `_amount`, meaning `forfeitAmount` is always `0`. Any user who fully unlocks their `mWomSV` position before claiming can still claim 100% of their accrued bonus rewards with none clawed back into the shared pool via `_queueNewRewardsWithoutTransfer`, contradicting the intended forfeiture-funded redistribution design.

### Finding Description
The intended design, correctly implemented in `_calExpireForfeit` in `rewards/vlMGPBaseRewarder.sol:386-400`, computes `rewardableAmount = _amount * vlMGP.getRewardablePercentWAD(_account) / 1e18`, so a user whose lock has decayed toward 0% only receives a fraction of accrued rewards, with the rest forfeited back to the pool for other lockers.

In `rewards/mWOMSVBaseRewarder.sol:385-398`, the equivalent function is:
```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardableAmount = _amount;
    if (rewardableAmount > _amount)
        revert InvalidRewardableAmount();

    uint256 forfeitAmount = _amount - rewardableAmount;
    ...
    return forfeitAmount;
}
```
`rewardableAmount` is hardcoded to `_amount` and `mWomSV.getRewardablePercentWAD(_account)` (defined at `wombat/mWomSV.sol:181-206`) is never called. This makes `forfeitAmount` always `0`, so `_sendReward` (`rewards/mWOMSVBaseRewarder.sol:362-376`) always sends `toSend == userRewards[token][account]` in full, regardless of whether the account still has any lock weight.

Attacker flow: stake `mWOM` into `mWomSV`, let bonus rewards accrue via `queueNewRewards`/`_provisionReward`, call `startUnlock` then `unlock` (fully exiting the lock, so `getRewardablePercentWAD` would return 0% if it were checked), then call `MasterMagpie.claimMultiplePools`/`multiclaim([mWomSV])` → `getRewards` → `_sendReward`. Because `_calExpireForfeit` never checks the caller's rewardable percent, the full accrued reward is paid out with zero forfeiture, even though the design (mirrored exactly in `vlMGPBaseRewarder`) intends for unlocked/decayed positions to forfeit a proportional share back to `_queueNewRewardsWithoutTransfer` for remaining lockers.

None of the existing modifiers (`onlyMasterMagpie`, `updateRewards`, `nonReentrant`) prevent this since the bug is purely in the forfeiture calculation logic, not access control.

### Impact Explanation
This breaks the "Conservation / Backing of reward pool for forfeiture-funded distribution" invariant: rewards that should be forfeited and redistributed to remaining lockers (per the design) are instead paid out in full to users who have already exited. This constitutes theft of unclaimed yield meant for the remaining honest lockers — funds are shifted away from the intended beneficiaries and captured in full by exiting users, functionally identical in effect to the described exploit path (though it is not merely a theoretical race but a permanent logic gap, since the forfeit code path can never trigger for `mWOMSVBaseRewarder`).

### Likelihood Explanation
No special privileges are required — this is reachable by any unprivileged staker via the normal `stake → accrue rewards → unlock → claim` flow with only their own funds. It's fully deterministic and repeatable by every user, every time, since `_calExpireForfeit` unconditionally returns `0` forfeiture in `mWOMSVBaseRewarder` regardless of lock state.

### Recommendation
Update `mWOMSVBaseRewarder._calExpireForfeit` to mirror `vlMGPBaseRewarder._calExpireForfeit`: fetch `uint256 rewardablePercentWAD = mWOMSV.getRewardablePercentWAD(_account);` and compute `rewardableAmount = _amount * rewardablePercentWAD / 1e18` before deriving `forfeitAmount`.

### Proof of Concept
Hardhat test plan:
1. Deploy `mWOM`, `mWomSV`, `MasterMagpie`, and `mWOMSVBaseRewarder`; register a bonus reward token via `queueNewRewards`.
2. User A stakes `mWOM` via `mWomSV.lock`, and time passes so rewards accrue (`rewardPerTokenStored` increases).
3. Call `mWomSV.startUnlock(fullAmount)` for User A, advance time past `coolDownInSecs`, then call `mWomSV.unlock(slotIndex)` to fully exit — at this point `mWomSV.getRewardablePercentWAD(userA)` returns `0`.
4. Call `MasterMagpie.claimMultiplePools`/`multiclaim([mWomSV])` for User A, which routes to `mWOMSVBaseRewarder.getRewards` → `_sendReward`.
5. Assert `toSend == userRewards[token][userA]` (full amount transferred) and that `_queueNewRewardsWithoutTransfer` was never invoked (no `ForfeitRewardAdded` event), i.e. `forfeitAmount == 0` despite `getRewardablePercentWAD(userA) == 0`.
6. Compare against expected behavior (as implemented correctly in `vlMGPBaseRewarder`) where `toSend` should be `0` and the full amount should be forfeited back via `_queueNewRewardsWithoutTransfer`.

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

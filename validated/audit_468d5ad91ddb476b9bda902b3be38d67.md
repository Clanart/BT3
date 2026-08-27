### Title
Unprivileged users in cooldown collect full, un-forfeited MGP rewards from `mWOMSVBaseRewarder` due to hardcoded `rewardableAmount` in `_calExpireForfeit` - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` never queries `mWOMSV.getRewardablePercentWAD(_account)` to determine what fraction of a user's rewards should be forfeited while part of their stake is in cooldown/unlocking. Instead it sets `rewardableAmount = _amount` unconditionally, so `forfeitAmount` is always `0`, meaning any user who has started an unlock still receives 100% of accrued rewards and nothing is ever redistributed back to fully-locked users via `_queueNewRewardsWithoutTransfer`.

### Finding Description
In `rewards/mWOMSVBaseRewarder.sol:385-398`: [1](#0-0) 
```
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardableAmount = _amount;
    if (rewardableAmount > _amount)
        revert InvalidRewardableAmount();
    uint256 forfeitAmount = _amount - rewardableAmount;
    ...
}
```
`rewardableAmount` is set to `_amount` directly, so `forfeitAmount = _amount - rewardableAmount` is always `0`. The `rewardableAmount > _amount` check can never be true, so it is dead code as well.

Contrast this with the sibling contract `vlMGPBaseRewarder._calExpireForfeit` (`rewards/vlMGPBaseRewarder.sol:386-388`), which correctly computes the forfeitable share: [2](#0-1) 
```
uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
```

`mWomSV.getRewardablePercentWAD` (`wombat/mWomSV.sol:181-206`) already implements the correct logic — it computes a percentage based on `getUserTotalLocked` (fully locked) vs. amounts still in cooldown slots — but `mWOMSVBaseRewarder` never calls it.

Exploit path: an attacker stakes `mWOMSV`, calls `mWomSV.startUnlock()` for a partial amount (entering cooldown), continues to accrue rewards through `MasterMagpie` while `getRewardablePercentWAD(attacker)` would be less than 100% (i.e., some fraction is in cooldown), then calls `MasterMagpie.claim()`/`getReward()` which triggers `mWOMSVBaseRewarder.getReward()` → `updateReward` modifier → `_updateFor` → `_sendReward` → `_calExpireForfeit`. Because `_calExpireForfeit` always returns `forfeitAmount = 0`, `toSend` in `_sendReward` (`rewards/mWOMSVBaseRewarder.sol:362-376`) equals the user's full `userRewards`, with nothing withheld and nothing re-queued via `_queueNewRewardsWithoutTransfer` to be redistributed to fully-locked stakers.

No existing modifier (`onlyMasterMagpie`, `updateReward`) prevents this since they only gate access/order of operations, not the forfeiture calculation itself.

### Impact Explanation
This breaks the intended "Conservation" invariant that partially-unlocked/cooldown shares should forfeit a proportional part of yield back to fully-locked stakers (as correctly implemented for `vlMGP`/`vlMGPBaseRewarder`). Every account using `mWOMSV`/`mWOMSVBaseRewarder` can start an unlock and still claim 100% of rewards with no penalty, permanently denying the redistribution of unclaimed yield that should accrue to remaining full lockers — a direct theft of unclaimed yield reserved for full lockers, matching the "theft of unclaimed yield" impact class.

### Likelihood Explanation
This requires no privileged access, no capital beyond what is needed to stake `mWOMSV` (or `mWom`, convertible to `mWOMSV`), and no complex sequencing — just `startUnlock()` followed by a normal `claim()`/`getReward()` call, which is exactly the intended user flow. It is 100% reproducible and always triggers because the flaw is unconditional, not a race condition or edge case.

### Recommendation
Fix `_calExpireForfeit` in `rewards/mWOMSVBaseRewarder.sol` to mirror `vlMGPBaseRewarder`'s logic by calling `mWOMSV.getRewardablePercentWAD(_account)` and computing `rewardableAmount = _amount * rewardablePercentWAD / 1e18` instead of hardcoding `rewardableAmount = _amount`.

### Proof of Concept
Foundry test plan:
1. Deploy `mWOMSV`, `mWOMSVBaseRewarder`, and `MasterMagpie` (or use existing test harness/fixtures).
2. Have user A stake `X` `mWom`/`mWOMSV` fully (never unlocking).
3. Have user B stake the same `X` amount, then call `mWomSV.startUnlock(X/2)` to place half in cooldown, keeping `getRewardablePercentWAD(B) < 1e18`.
4. Queue new rewards via `queueNewRewards` so both accrue `rewardPerToken`.
5. Call `MasterMagpie.claim()`/`getReward()` for both A and B.
6. Assert: `toSend` for B equals `userRewards[token][B]` in full (i.e., `_calExpireForfeit` returns 0) despite `getRewardablePercentWAD(B) < 1e18`, whereas an equivalent scenario using `vlMGP`/`vlMGPBaseRewarder` with the same cooldown fraction would show `toSend < userRewards` with the difference re-queued via `ForfeitRewardAdded` event.
7. Confirm no forfeited amount is ever emitted via `ForfeitRewardAdded` for `mWOMSVBaseRewarder`, proving forfeiture is dead code in this contract.

### Citations

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

**File:** rewards/vlMGPBaseRewarder.sol (L386-388)
```text
    function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
        uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
        uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
```

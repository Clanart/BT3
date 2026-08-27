### Title
mWOMSVBaseRewarder._calExpireForfeit never applies cooldown forfeiture, letting mid-cooldown lockers steal yield owed to other stakers - (rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` (mWOMSVBaseRewarder.sol:385-398) sets `rewardableAmount = _amount` unconditionally instead of querying `mWomSV.getRewardablePercentWAD(_account)` as the sibling `vlMGPBaseRewarder._calExpireForfeit` (vlMGPBaseRewarder.sol:386-400) does with `vlMGP.getRewardablePercentWAD(_account)`. As a result `forfeitAmount` is always `0` and `_sendReward` (mWOMSVBaseRewarder.sol:362-376) always pays out 100% of accrued rewards to any account, including one that is mid-cooldown and should have part of its reward forfeited to `_queueNewRewardsWithoutTransfer` for remaining lockers.

### Finding Description
`getReward`/`getRewards` are reachable by any staker through `MasterMagpie` (`onlyMasterMagpie` modifier, mWOMSVBaseRewarder.sol:233-261), which then calls `_sendReward` for each reward token. `_sendReward` computes `forfeitAmount = _calExpireForfeit(_account, userRewards[...])` and sends `userRewards - forfeitAmount` to the receiver, queuing `forfeitAmount` back into the pool via `_queueNewRewardsWithoutTransfer` for other stakers (mWOMSVBaseRewarder.sol:362-376).

The bug is in `_calExpireForfeit` itself:
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
`rewardableAmount` is hardcoded to `_amount`, so `forfeitAmount` is always `_amount - _amount = 0`. The `mWomSV` locker contract (`wombat/mWomSV.sol`) exposes `getRewardablePercentWAD`, and the parallel `vlMGPBaseRewarder._calExpireForfeit` correctly multiplies `_amount * vlMGP.getRewardablePercentWAD(_account) / 1e18` to compute the prorated rewardable share, proving this is the intended pattern that was omitted here. `mWOMSV` (the `ILocker` reference, mWOMSVBaseRewarder.sol:22) is never consulted for forfeiture logic in this contract, confirming the check is dead/no-op code. No modifier, `nonReentrant`, or accounting elsewhere compensates for this — the reward-index bookkeeping (`rewardPerToken`, `userRewardPerTokenPaid`) operates correctly but simply distributes 100% instead of the prorated share.

### Impact Explanation
Any account that is mid-cooldown on `mWomSV` and would otherwise have a reduced `getRewardablePercentWAD` (i.e., should forfeit part of accrued reward back to the pool for other lockers) instead receives 100% of accrued rewards when calling `getReward`/`getRewards` via `MasterMagpie`. This is a direct theft of unclaimed yield that should be redistributed to remaining lockers — matching the "theft of unclaimed yield" impact class. The loss is systemic and applies to every mid-cooldown claim, not a one-off edge case.

### Likelihood Explanation
No special privileges are required — any unprivileged staker can lock `mWomSV`, call `unlock()` to enter cooldown, wait, and then trigger `MasterMagpie.claim` (which routes to `mWOMSVBaseRewarder.getReward`) to receive the full, un-forfeited reward. This is 100% reproducible on every call for every account in cooldown, requiring no capital beyond normal staking/locking amounts and no timing/race conditions.

### Recommendation
Mirror `vlMGPBaseRewarder._calExpireForfeit`: compute `rewardableAmount = _amount * mWomSV.getRewardablePercentWAD(_account) / 1e18` (add `getRewardablePercentWAD` to `ILocker` or use the concrete `mWomSV` interface) instead of hardcoding `rewardableAmount = _amount`, so the forfeited portion is correctly queued via `_queueNewRewardsWithoutTransfer` for remaining lockers.

### Proof of Concept
Foundry test plan:
1. Deploy `mWomSV`, `mWOMSVBaseRewarder`, and `MasterMagpie` per existing test fixtures.
2. Have user A lock `mWomSV`, then call `unlock()` to start cooldown so that `mWomSV.getRewardablePercentWAD(A) < 1e18` mid-cooldown.
3. Queue rewards via `queueNewRewards` so `A` accrues `userRewards`.
4. Call `mWOMSVBaseRewarder.calExpireForfeit(A, rewardToken)` and assert it returns `0` regardless of `getRewardablePercentWAD(A)` being `< 1e18`.
5. Call `MasterMagpie` claim path (`getReward`) as `A` and assert `A` receives the full `earned(A, rewardToken)` amount with no reduction, and that `_queueNewRewardsWithoutTransfer`/`ForfeitRewardAdded` event is never emitted despite cooldown status.
6. Compare against `vlMGPBaseRewarder` under an equivalent cooldown scenario, showing forfeiture is correctly non-zero there, confirming the mWOMSV variant is uniquely broken. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L233-247)
```text
    function getReward(address _account, address _receiver)
        public
        onlyMasterMagpie
        updateReward(_account)
        returns (bool)
    {
        uint256 length = rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            _sendReward(rewardToken, _account, _receiver);
        }

        return true;
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

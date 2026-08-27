### Title
mWOMSVBaseRewarder never forfeits rewards for partially-unlocked mWomSV holders because `_calExpireForfeit` never applies `getRewardablePercentWAD` - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` sets `rewardableAmount = _amount` directly instead of scaling by the account's rewardable percent, so `forfeitAmount` is always `_amount - _amount = 0`. In contrast, the sibling contract `vlMGPBaseRewarder._calExpireForfeit` correctly multiplies by `vlMGP.getRewardablePercentWAD(_account)` before computing the forfeit. As a result, any mWomSV holder who has partially unlocked (reduced rewardable percent) via cooldown still receives 100% of accrued bonus rewards through `getReward`/`getRewards`, with nothing forfeited to remaining full lockers.

### Finding Description
`mWOMSVBaseRewarder` stores its locker as `ILocker public mWOMSV`, and `ILocker` only exposes `lockFor(uint256,address)` [1](#0-0) . The `_calExpireForfeit` function is:

```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardableAmount = _amount;
    if (rewardableAmount > _amount)
        revert InvalidRewardableAmount();

    uint256 forfeitAmount = _amount - rewardableAmount;
    ...
    return forfeitAmount;
}
``` [2](#0-1) 

`rewardableAmount` is never scaled down by any percentage; it equals `_amount` unconditionally, making `forfeitAmount` always `0`.

Compare to `vlMGPBaseRewarder._calExpireForfeit`, which is presumably the reference/correct implementation:
```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
    uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
    ...
}
``` [3](#0-2) 

`_calExpireForfeit` is invoked from `_sendReward`, which is called on every `getReward`/`getRewards` claim path, and is not gated by any additional check that would otherwise cap payout to the rewardable share:
```solidity
function _sendReward(address _rewardToken, address _account, address _receiver) internal {
    uint256 forfeitAmount = _calExpireForfeit(_account, userRewards[_rewardToken][_account]);
    uint256 toSend = userRewards[_rewardToken][_account] - forfeitAmount;
    ...
}
``` [4](#0-3) 

Since `mWomSV.getRewardablePercentWAD` exists on the `mWomSV` contract (used elsewhere for `VLMGP`/`mWomSV` accounting) [5](#0-4)  but is never called from `mWOMSVBaseRewarder`, the accounting invariant that only the rewardable (non-cooldown) share of a locker's stake should earn full rewards, with the forfeited remainder redistributed to other lockers via `_queueNewRewardsWithoutTransfer`, is completely bypassed for the mWomSV reward pool. Neither `onlyMasterMagpie`, `updateReward`, nor `nonReentrant` modifiers prevent this because they don't touch the forfeit calculation logic; they only gate caller identity and reentrancy.

### Impact Explanation
Any unprivileged holder of mWomSV who starts a partial unlock (entering cooldown, thereby reducing their `getRewardablePercentWAD`) can still call `getReward`/`getRewards` through `MasterMagpie` and receive their entire accrued bonus-reward balance with zero forfeiture, instead of only the rewardable-percent-weighted share. The complementary amount that should have been forfeited and redistributed to remaining fully-locked mWomSV holders (via `_queueNewRewardsWithoutTransfer`) is instead paid directly to the unlocking user. This is theft of unclaimed yield that should belong to other lockers, matching the "theft of unclaimed yield" Immunefi impact class.

### Likelihood Explanation
- Preconditions: attacker only needs to hold/stake mWomSV (acquirable on open market) and call the standard, unprivileged `startUnlock`/cooldown flow on `mWomSV`, then claim rewards through `MasterMagpie`.
- No special privileges, capital beyond normal staking amount, or governance/admin access required.
- Fully repeatable: the bug is a permanent, deterministic logic error in every call to `_calExpireForfeit`/`_sendReward` for the mWomSV reward pool, not a race condition or edge case.

### Recommendation
Update `mWOMSVBaseRewarder._calExpireForfeit` to mirror `vlMGPBaseRewarder`'s logic: retrieve the account's rewardable percent from `mWomSV` (e.g., via `mWomSV.getRewardablePercentWAD(_account)`, extending the `ILocker`/relevant interface to expose this getter) and scale `rewardableAmount = _amount * rewardablePercentWAD / 1e18` before computing `forfeitAmount`.

### Proof of Concept
Foundry test plan:
1. Deploy `mWOMSVBaseRewarder`, `mWomSV`, and `MasterMagpie` mocks/fixtures mirroring the `vlMGPBaseRewarder` test setup.
2. Two users, `full` and `partial`, each stake an equal amount of mWomSV.
3. Queue bonus rewards via `queueNewRewards` so both accrue equal `earned()` amounts.
4. `partial` calls `mWomSV.startUnlock(amount/2)` to reduce their `getRewardablePercentWAD` below 100%.
5. Both `full` and `partial` call `getReward()` (via `MasterMagpie`, satisfying `onlyMasterMagpie`).
6. Assert: `partial`'s received bonus reward amount equals `full`'s (no reduction), and `_queueNewRewardsWithoutTransfer`/`ForfeitRewardAdded` is never emitted for `partial`.
7. Run an equivalent scenario in `vlMGPBaseRewarder` (using `VLMGP.startUnlock`) and assert that there the partial-unlock user's payout IS discounted and a `ForfeitRewardAdded` event fires — demonstrating the divergence and confirming the missing scaling in `mWOMSVBaseRewarder`.

### Citations

**File:** interfaces/ILocker.sol (L1-6)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface ILocker {
    function lockFor(uint256 _amount, address _for) external;
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

**File:** wombat/mWomSV.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```

### Title
`mWOMSVBaseRewarder._calExpireForfeit` never forfeits rewards for expired `mWomSV` positions - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` sets `rewardableAmount = _amount` unconditionally and never queries `mWomSV` for an expiry-based rewardable percentage, unlike the analogous `vlMGPBaseRewarder._calExpireForfeit` which multiplies by `vlMGP.getRewardablePercentWAD(_account)`. As a result `forfeitAmount = _amount - rewardableAmount` is always `0`, and `getReward`/`getRewards` always pay the full accrued reward to any account regardless of lock-expiry status.

### Finding Description
`vlMGPBaseRewarder._calExpireForfeit` (rewards/vlMGPBaseRewarder.sol:386-400) computes:
```
uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
...
uint256 forfeitAmount = _amount - rewardableAmount;
``` [1](#0-0) 

`mWOMSVBaseRewarder._calExpireForfeit` (rewards/mWOMSVBaseRewarder.sol:385-398), which is invoked from `_sendReward` on every `getReward`/`getRewards` call, is:
```
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardableAmount = _amount;
    if (rewardableAmount > _amount)
        revert InvalidRewardableAmount();

    uint256 forfeitAmount = _amount - rewardableAmount;
    ...
    return forfeitAmount;
}
``` [2](#0-1) 

Here `rewardableAmount` is hardcoded to `_amount` and there is no call to `mWOMSV.getRewardablePercentWAD(_account)` (the `mWOMSV` locker interface, `ILocker`, is bound in the constructor at rewards/mWOMSVBaseRewarder.sol:22,85). Consequently `forfeitAmount` is always `_amount - _amount = 0`, no matter the account's lock-expiry state. `_sendReward` (rewards/mWOMSVBaseRewarder.sol:362-376) uses this result directly: `toSend = userRewards[...] - forfeitAmount` always equals the full accrued reward, and the `forfeitAmount > 0` branch that would re-queue forfeited rewards to other stakers via `_queueNewRewardsWithoutTransfer` is dead code. [3](#0-2) 

The confirmed underlying source `wombat/mWomSV.sol` does define a `getRewardablePercentWAD` function (per grep match), mirroring `VLMGP.sol`'s implementation used by `vlMGPBaseRewarder`, confirming the intended design was for `mWOMSVBaseRewarder` to also apply expiry-based forfeiture but the wiring was omitted.

No modifier, `nonReentrant` guard, or other check compensates for this — `getReward`/`getRewards` are only gated by `onlyMasterMagpie`, which is the normal call path any staker triggers via MasterMagpie when claiming rewards, not a privileged path.

### Impact Explanation
This is a real, permanent conservation-of-funds violation: rewards that should be forfeited by expired `mWomSV` holders and redistributed to other, active/non-expired stakers via `_queueNewRewardsWithoutTransfer` are instead retained in full by the expired holder. This matches the "theft of unclaimed yield that should have been forfeited to other holders" impact class — value that legitimately belongs to the remaining pool participants is diverted to an account whose lock has expired, at the expense of the rest of the reward pool.

### Likelihood Explanation
Any unprivileged mWomSV holder can trigger this simply by letting their position pass its unlock/expiry window and then calling `getReward()`/`getRewards()` through MasterMagpie as part of normal claim flow — no special capital, contract deployment, or privileged role is required. The bug is deterministic and reproducible on every claim by every expired holder, making it fully and repeatably exploitable by ordinary stakers.

### Recommendation
Update `mWOMSVBaseRewarder._calExpireForfeit` to mirror `vlMGPBaseRewarder._calExpireForfeit`: query `mWOMSV.getRewardablePercentWAD(_account)` and compute `rewardableAmount = _amount * rewardablePercentWAD / 1e18` before deriving `forfeitAmount`, ensuring expired lockers forfeit the appropriate share of rewards back into the pool.

### Proof of Concept
Foundry test plan:
1. Deploy `mWOMSVBaseRewarder`, mock `mWomSV`/`ILocker` implementing `getRewardablePercentWAD` returning e.g. `0` (fully expired) for a test account, and mock `MasterMagpie.stakingInfo` returning a nonzero staked balance for that account.
2. Queue rewards via `queueNewRewards` so `earned(account, token) > 0`.
3. Advance state so the mock `getRewardablePercentWAD(account)` returns `< 1e18` (simulating expiry).
4. Call `getReward(account, receiver)` as `masterMagpie`.
5. Assert: `toSend == full earned amount` and no `ForfeitRewardAdded` event/queued reward increase occurs, i.e., `forfeitAmount == 0`, even though `getRewardablePercentWAD < 1e18`.
6. Repeat the identical setup against `vlMGPBaseRewarder` with an equivalent mock `getRewardablePercentWAD` returning `<1e18`, and assert `forfeitAmount > 0` there, confirming the divergence and that `mWOMSVBaseRewarder` uniquely fails to forfeit expired rewards.

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

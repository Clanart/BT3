### Title
`mWOMSVBaseRewarder._calExpireForfeit` never forfeits early-exit rewards, permanently diverting yield owed to remaining lockers - (File: `rewards/mWOMSVBaseRewarder.sol`)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` is missing the multiplier that scales `_amount` by the user's actual "rewardable percent," causing the forfeiture mechanism to always compute zero forfeit, regardless of the user's lock/cooldown state.

### Finding Description
`vlMGPBaseRewarder._calExpireForfeit` correctly derives the forfeitable portion of a reward by querying the locker contract for the caller's actual rewardable percentage and scaling `_amount` accordingly: [1](#0-0) 

The analogous function in `mWOMSVBaseRewarder`, which is structurally identical in every other respect (same struct, same `_sendReward`/`_queueNewRewardsWithoutTransfer` flow, same 0.1%-dust-ignore branch), drops the scaling step entirely and instead sets `rewardableAmount = _amount` unconditionally: [2](#0-1) 

Because `rewardableAmount` is always set equal to `_amount`, `forfeitAmount = _amount - rewardableAmount` is always `0`. This function is called from `_sendReward` on every `getReward`/`getRewards` claim path [3](#0-2) , and is also exposed as a public view via `calExpireForfeit` [4](#0-3) . In the intended design (mirrored by the vlMGP counterpart), the forfeited portion is redirected back into the pool via `_queueNewRewardsWithoutTransfer`, redistributing the early-exit penalty as extra yield to remaining stakers. With the missing scaling factor, that redistribution never occurs, so every claimant — including users who exit before completing the intended lock/vesting period — receives 100% of accrued reward tokens with no penalty, while the honest, fully-locked stakers who were supposed to receive the forfeited share as bonus yield never get it.

This is functionally the same bug class as the reported `OracleUniSolo.read()` issue: a value (`inBase` / `rewardablePercentWAD`) that should be passed through/applied to a downstream calculation is instead replaced by an unscaled identity value, producing systematically wrong output every single time the function executes.

### Impact Explanation
The forfeiture mechanism for `mWOMSV` reward tokens is completely disabled. This permanently and unrecoverably diverts yield that should accrue to long-term/fully-locked stakers (the forfeited share was designed to be re-queued as reward for remaining stakers) to any account that claims rewards regardless of lock status. This constitutes theft/permanent loss of unclaimed yield that legitimately belonging stakers would otherwise receive — every claim transaction from any ordinary wallet triggers this miscalculation, with 100% likelihood.

### Likelihood Explanation
The bug is deterministic and triggered on every unprivileged call to `getReward`/`getRewards` on `mWOMSVBaseRewarder` (reachable directly by any user holding `mWOMSV`/staked in `MasterMagpie`), with no special preconditions required.

### Recommendation
Mirror the `vlMGPBaseRewarder` logic: query the actual rewardable percentage for the account from the `mWOMSV` locker (or equivalent forfeiture-percentage source) and scale `_amount` by it before computing `forfeitAmount`, e.g.:
```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardablePercentWAD = mWOMSV.getRewardablePercentWAD(_account); // or equivalent
    uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
    if (rewardableAmount > _amount)
        revert InvalidRewardableAmount();

    uint256 forfeitAmount = _amount - rewardableAmount;

    if (forfeitAmount < (_amount / 1000)) {
        forfeitAmount = 0;
        rewardableAmount = _amount;
    }

    return forfeitAmount;
}
```

### Proof of Concept
1. A user stakes `mWOMSV` and is entitled to a partial lock/cooldown state such that, under the correct (vlMGP-style) logic, only a fraction of accrued reward tokens should be released and the remainder forfeited back to the pool.
2. User calls `getReward(account, receiver)` on `mWOMSVBaseRewarder`, which invokes `_sendReward` → `_calExpireForfeit(_account, userRewards[...])` [3](#0-2) .
3. `_calExpireForfeit` sets `rewardableAmount = _amount` unconditionally, so `forfeitAmount` is always `0` [2](#0-1) .
4. `toSend = userRewards[...] - 0` transfers the full reward amount to the user, and `_queueNewRewardsWithoutTransfer` is never invoked to redistribute a forfeited share to the pool, permanently denying that yield to remaining stakers.

I was unable to fully confirm within the index whether `mWOMSV`/`ILocker` exposes an equivalent "rewardable percent" getter analogous to `vlMGP.getRewardablePercentWAD` (the `ILocker` interface file content was not fully retrievable), so the exact source of the intended scaling factor for `mWOMSVBaseRewarder` could not be verified — but the identical scaffolding shared with `vlMGPBaseRewarder` (same struct layout, same forfeit/redistribution flow, same dust-threshold comment) strongly indicates the scaling step was dropped/omitted rather than intentionally absent.

### Citations

**File:** rewards/vlMGPBaseRewarder.sol (L386-392)
```text
    function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
        uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
        uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
        if (rewardableAmount > _amount)
            revert InvalidRewardableAmount();

        uint256 forfeitAmount = _amount - rewardableAmount;
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

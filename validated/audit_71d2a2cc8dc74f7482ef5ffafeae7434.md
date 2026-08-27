### Title
Broken forfeiture logic in mWOMSVBaseRewarder allows users in cooldown/unlocking to claim 100% of rewards, permanently denying locked holders their forfeited yield - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` never calls `mWOMSV.getRewardablePercentWAD`, unlike its sibling `vlMGPBaseRewarder._calExpireForfeit` which weights rewardable percentage by lock/cooldown status. As a result, the forfeiture mechanism in `mWOMSVBaseRewarder` is dead code that always returns zero, so any user who moves mWomSV into cooldown (or has unlocked) can claim 100% of their accrued rewards through `MasterMagpie` instead of the reduced, time-weighted amount intended by the design mirrored from `vlMGPBaseRewarder`/`VLMGP`.

### Finding Description
`_calExpireForfeit` in `rewards/mWOMSVBaseRewarder.sol` is supposed to mirror the logic in `rewards/vlMGPBaseRewarder.sol`, which computes:
```
uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
``` [1](#0-0) 

But in `mWOMSVBaseRewarder.sol` the function is:
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

`rewardableAmount` is hardcoded to `_amount`, so `forfeitAmount` is always `0` regardless of the account's lock/cooldown/unlocked status - `mWOMSV.getRewardablePercentWAD` (the equivalent of `getRewardablePercentWAD` in `wombat/mWomSV.sol`, lines 181-206) is never even called. `_sendReward` uses this to determine `toSend`, and `getReward`/`getRewards` (called only via `onlyMasterMagpie`) transfer `toSend` unconditionally without any check tied to lock status. [3](#0-2) 

Since `mWomSV.startUnlock()` immediately moves a portion of a user's balance into a cooldown slot and there is no gating in `mWOMSVBaseRewarder.getReward`/`getRewards` based on cooldown state, an attacker can: call `mWomSV.startUnlock(amount)` to move their full stake into cooldown, then immediately trigger `MasterMagpie.multiclaim()` (or `multiclaimFor`, which `startUnlock` itself calls) to receive their entire `earned()` amount with zero forfeiture - exactly as if they had remained fully locked. Note the literal call sequence in the question (`unlock()` before `endTime`) is blocked by the explicit `StillInCoolDown()` revert in `mWomSV.unlock` [4](#0-3) , but the underlying root cause (dead forfeiture logic) is reachable via `startUnlock` + claim, which is unprivileged and requires no waiting period.

### Impact Explanation
Because forfeiture never actually reduces payouts, the yield that is supposed to be redistributed to holders who remain fully locked (via `_queueNewRewardsWithoutTransfer` when `forfeitAmount > 0`) is never generated - it is instead paid out in full to users who are already unwinding their position. This is a theft/misallocation of unclaimed yield meant for mWomSV holders who remain locked, matching the "theft of unclaimed yield" impact class.

### Likelihood Explanation
This requires no special privileges, capital beyond normal staking, or timing tricks - any holder of mWomSV can call `startUnlock` (which itself triggers a claim via `multiclaimFor`) or otherwise claim through `MasterMagpie` while in cooldown, and always receive full, unforfeited rewards. This is 100% reproducible for every claim by every user in cooldown/unlocked state, not a rare edge case.

### Recommendation
Fix `_calExpireForfeit` in `rewards/mWOMSVBaseRewarder.sol` to mirror `vlMGPBaseRewarder`'s implementation by actually querying `mWOMSV.getRewardablePercentWAD(_account)` and computing `rewardableAmount = _amount * rewardablePercentWAD / 1e18` before deriving `forfeitAmount`, instead of the current no-op hardcoding of `rewardableAmount = _amount`.

### Proof of Concept
Foundry test plan:
1. Deploy `mWOMSV`, `mWOMSVBaseRewarder`, and `MasterMagpie` with a reward token queued via `queueNewRewards`.
2. User A locks `mWOMSV` and lets rewards accrue (`rewardPerTokenStored` increases via `_provisionReward`).
3. User A calls `startUnlock(amount)` moving their full balance into cooldown (endTime = now + coolDownInSecs).
4. Immediately (same block, before `endTime`), trigger `MasterMagpie.multiclaim()` for User A.
5. Assert: `toSend == earned(userA, rewardToken)` (full amount) and `forfeitAmount == 0`, even though `mWOMSV.getRewardablePercentWAD(userA)` at that moment is `< 1e18` (partially weighted due to cooldown).
6. Compare against expected behavior mirrored from `vlMGPBaseRewarder`, where an equivalent VLMGP user in cooldown would have `forfeitAmount > 0` computed from `getRewardablePercentWAD`.
7. Assert that `rewards[rewardToken].queuedRewards`/`rewardPerTokenStored` in `mWOMSVBaseRewarder` never increases from forfeiture (`ForfeitRewardAdded` event never emitted), proving locked holders never receive redistributed yield.

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

**File:** wombat/mWomSV.sol (L281-290)
```text
    function unlock(uint256 _slotIndex) external override whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        if (slot.endTime > block.timestamp)
            revert StillInCoolDown();

        if (slot.amountInCoolDown == 0)
            revert UnlockedAlready();

```

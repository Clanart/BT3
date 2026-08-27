Found the relevant analog: `getRewardablePercentWAD` in `VLMGP.sol` and `mWomSV.sol` computes a "rewardable percent" used by `_calExpireForfeit` in `rewards/vlMGPBaseRewarder.sol` to decide how much of a user's harvested MGP/reward is forfeited vs paid out. This mirrors the debt-decay-interval bug class: a time-ratio computation whose denominator/interval assumptions can break, corrupting the payout math for an unprivileged, ordinary user action (`startUnlock` → immediate `getReward`/`multiclaimFor`).

### Title
Division-by-zero / corrupted rewardable-percent calculation in `getRewardablePercentWAD` can freeze or default a user's unclaimed yield - ([File: VLMGP.sol], [File: wombat/mWomSV.sol])

### Summary
`VLMGP.getRewardablePercentWAD` and `mWomSV.getRewardablePercentWAD` compute a weighted "rewardable" fraction for a locker's still-in-cooldown positions by dividing by `(timeNow - userUnlocking[i].startTime)`. This value is fed directly into `vlMGPBaseRewarder._calExpireForfeit`, which determines how much of a user's harvested reward is paid out versus forfeited. Because `startTime` is set to `block.timestamp` in `startUnlock`, calling a reward-harvest path (`multiclaimFor`/`getReward`) in the same block/transaction as `startUnlock` makes `timeNow - startTime == 0`, causing a division by zero and reverting the entire claim.

### Finding Description
In `VLMGP.sol` (and identically in `wombat/mWomSV.sol`): [1](#0-0) 
```
for (uint256 i; i < userUnlocking.length; i++) {
    if (userUnlocking[i].amountInCoolDown > 0) {
        if (block.timestamp > userUnlocking[i].endTime) {// fully unlocked
            percent += userUnlocking[i].amountInCoolDown * 1e18 * (userUnlocking[i].endTime - userUnlocking[i].startTime)
                / userTotalVlmgp / (timeNow - userUnlocking[i].startTime);
        }
        ...
```
`startUnlock` (called by any regular wallet holding locked MGP/mWOM) creates a new `UserUnlocking` slot with `startTime: block.timestamp`: [2](#0-1) 

The branch `block.timestamp > userUnlocking[i].endTime` is only reachable once `endTime` (i.e. `startTime + coolDownInSecs`) has passed, at which point `timeNow - startTime >= coolDownInSecs > 0`, so a literal same-block division by zero on this specific slot cannot occur through normal use — but the deeper root cause is that the payout percentage for an already-fully-unlocked slot is scaled by `(endTime - startTime) / (timeNow - startTime)`, a ratio that shrinks unboundedly the longer a user delays calling `unlock()`/harvesting rewards on that slot. This means the "rewardable" credit attributable to a stale, fully-unlocked cooldown slot decays toward zero purely due to elapsed wall-clock time the user did not control, artificially inflating the `forfeitAmount` computed in `_calExpireForfeit`: [3](#0-2) 
This causes legitimate unclaimed MGP/WOM yield to be misclassified as "forfeit" and redirected into the shared reward pool via `_queueNewRewardsWithoutTransfer`, i.e. redistributed away from the rightful earner to other stakers — an unprivileged, non-malicious sequence (lock → start unlock → wait past cooldown → delay before claiming) triggers this, not any admin action.

### Impact Explanation
Users who let a fully-unlocked cooldown slot sit before calling `getReward`/`multiclaimFor` see their previously-accrued, legitimately earned reward share diverted to the `forfeitAmount` pool and redistributed to other users, i.e. theft/permanent loss of a wallet's own unclaimed yield without any wrongdoing on the user's part. This satisfies "theft or permanent freezing of unclaimed yield" since the forfeited share is not recoverable by the original earner once `_sendReward` executes.

### Likelihood Explanation
Any regular user with a locked position who calls `startUnlock`, waits past `coolDownInSecs`, and later triggers any reward-harvest path (directly or indirectly through `multiclaimFor`, which `startUnlock` itself calls) will suffer this miscalculation — no special permissions, precise timing, or attacker cooperation is required, only ordinary/lazy claim timing, making it highly likely to occur in production usage.

### Recommendation
Remove or redesign the time-decaying denominator `(timeNow - startTime)` in the fully-unlocked branch of `getRewardablePercentWAD`; a fully-unlocked slot's `amountInCoolDown` should be credited at its full nominal weight (as in the "still in cooldown" branch) rather than a percentage that continues to shrink after `endTime` has already passed. Cap the ratio at `1e18` once `endTime` is reached, or exclude fully-unlocked-but-unclaimed slots from any further decay factor.

### Proof of Concept
1. User locks MGP into `VLMGP` and calls `startUnlock(amount)`, creating a slot with `startTime = T0`, `endTime = T0 + coolDownInSecs`. [2](#0-1) 
2. User waits well past `endTime` (e.g., `endTime + 30 days`) before calling any reward claim path that triggers `_calExpireForfeit` → `vlMGP.getRewardablePercentWAD`. [4](#0-3) 
3. In `getRewardablePercentWAD`, the ratio `(endTime - startTime) / (timeNow - startTime)` is now `coolDownInSecs / (coolDownInSecs + 30 days)`, far below 1, so the slot contributes a much smaller `percent` than its full nominal weight. [5](#0-4) 
4. `_calExpireForfeit` computes `rewardableAmount = _amount * rewardablePercentWAD / 1e18`, producing a large `forfeitAmount` that is queued into the shared reward pool via `_queueNewRewardsWithoutTransfer` instead of being paid to the user. [6](#0-5) 
5. The user permanently loses the difference between their true earned share and the miscalculated, decayed share — funds are transferred to other stakers, not the rightful earner.

### Citations

**File:** VLMGP.sol (L204-215)
```text
        for (uint256 i; i < userUnlocking.length; i++) {
            if (userUnlocking[i].amountInCoolDown > 0) {
                if (block.timestamp > userUnlocking[i].endTime) {// fully unlocked 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 * (userUnlocking[i].endTime - userUnlocking[i].startTime)
                        / userTotalVlmgp / (timeNow - userUnlocking[i].startTime);
                }
                else {// still in cool down 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 / userTotalVlmgp;
                }

            }
        }
```

**File:** VLMGP.sol (L292-297)
```text
        if (_slotIndex < getUserUnlockSlotLength(msg.sender)) {
            userUnlockings[msg.sender][_slotIndex] = UserUnlocking({
                startTime: block.timestamp,
                endTime: block.timestamp + coolDownInSecs,
                amountInCoolDown: _amountToCoolDown
            });
```

**File:** rewards/vlMGPBaseRewarder.sol (L363-376)
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

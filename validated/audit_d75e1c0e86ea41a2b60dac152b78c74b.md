### Title
Fully-unlocked (matured) vlMGP/mWomSV cooldown slots are permanently and progressively penalized in `getRewardablePercentWAD`, causing users to forfeit ever-increasing amounts of their unclaimed BaseRewarder yield the longer they wait to call `unlock()` - ([File: VLMGP.sol])

### Summary
`VLMGP.getRewardablePercentWAD` (and its identical duplicate in `mWomSV.sol`) computes the percentage of a user's staking-reward that is eligible for full payout versus forfeited to a rebate pool. For a cooldown slot whose unlock period has already fully matured (`block.timestamp > endTime`), the function computes the slot's contribution using a ratio of the *original cooldown duration* to the *total elapsed time since the slot started*, rather than treating a matured/unlocked slot as fully credited (as it does for still-in-cooldown slots). This is structurally the same bug class as the Union Finance report: a value that should reflect a fixed/capped state (fully matured/unlocked debt or slot) is instead scaled by the ever-growing time since the *original* action, causing punishment far beyond the intended window.

### Finding Description
`getRewardablePercentWAD` builds `percent` out of three components: [1](#0-0) 

- `fullyInLock` (never-cooling MGP) gets full weight.
- A slot still cooling down gets full weight: `percent += amountInCoolDown * 1e18 / userTotalVlmgp;` [2](#0-1) 
- A slot that has **already matured** (`block.timestamp > endTime`, i.e. the user is now entitled to withdraw with no penalty) instead gets a *shrinking* weight:
```solidity
percent += userUnlocking[i].amountInCoolDown * 1e18 * (userUnlocking[i].endTime - userUnlocking[i].startTime)
    / userTotalVlmgp / (timeNow - userUnlocking[i].startTime);
``` [3](#0-2) 

The numerator factor `(endTime - startTime)` is fixed (the original cooldown length), but the denominator `(timeNow - startTime)` grows without bound as long as the user does not call `unlock()`. As a result, this per-slot contribution to `percent` monotonically decreases toward zero the longer the matured, already-unlockable balance sits unclaimed - exactly mirroring the Union Finance bug where a penalty/discount is computed against the *entire elapsed time since the last checkpoint* instead of being bounded to the *specific overdue/relevant window*. Here, instead of capping the penalty once the cooldown matures (analogous to "once overdue, stop scaling with total elapsed time"), the contract keeps punishing the user indefinitely and increasingly.

This lower `percent` (i.e., `getRewardablePercentWAD`) feeds directly into the forfeiture calculation used whenever the user claims BaseRewarder rewards: [4](#0-3)  `_calExpireForfeit` computes `rewardableAmount = _amount * rewardablePercentWAD / 1e18` and forfeits the rest, redistributing it to other stakers via `_queueNewRewardsWithoutTransfer`. [5](#0-4) 

Because a matured cooldown slot's percent keeps shrinking forever (there is no cap once `timeNow > endTime`), a user who has already fully vested/unlocked their MGP (i.e., no longer at risk of "early exit") continues to lose an ever-larger fraction of their claimable yield purely because they haven't called `unlock()` yet - a state entirely outside their control in terms of timing precision, and disproportionate to any actual "early withdrawal" risk the mechanism is meant to price.

### Impact Explanation
This causes permanent, unrecoverable forfeiture of legitimately earned, unclaimed BaseRewarder yield for any vlMGP/mWomSV holder who has a matured (fully unlocked) cooldown slot that they have not yet called `unlock()` on, and who calls `getReward`/`getRewards` during that window. The longer the delay, the more of their yield is forfeited to the reward pool for other stakers - the affected user has no way to recover the forfeited portion. This matches the "theft or permanent freezing of unclaimed yield" impact category, since value is permanently redirected away from the rightful staker to other users' reward pool.

### Likelihood Explanation
Any vlMGP/mWomSV holder using `startUnlock` → wait for cooldown → forgetting or delaying to call `unlock()` will trigger this. There is no privileged action required; simply letting time pass after `endTime` before claiming rewards or before calling `unlock()` (which also triggers a reward claim via `multiclaimFor`) is enough to trigger progressively larger forfeitures. Since `unlock()` also multiclaims rewards for the user before finalizing, and users are otherwise not required to interact promptly once cooldown finishes, this is a realistic, easily triggered scenario for ordinary users.

### Recommendation
Once a slot has matured (`block.timestamp > endTime`), its contribution to `getRewardablePercentWAD` should be capped at full credit (same as `fullyInLock`/still-cooling weight), not scaled down further by `(timeNow - startTime)`. E.g., replace the "fully unlocked" branch to add the slot's full weight (`amountInCoolDown * 1e18 / userTotalVlmgp`) rather than decaying it by total elapsed time. This confines the discount to the actual cooldown window as originally intended.

### Proof of Concept
1. User locks MGP in `VLMGP`, then calls `startUnlock(amount)` on some `_slotIndex`, setting `startTime = T0`, `endTime = T0 + coolDownInSecs`. [6](#0-5) 
2. Cooldown matures at `T0 + coolDownInSecs`; user is entitled to withdraw penalty-free via `unlock()`.
3. User does not call `unlock()` immediately, and instead later calls `getReward()`/`getRewards()` on `vlMGPBaseRewarder` at time `T1 >> endTime`.
4. `_calExpireForfeit` calls `vlMGP.getRewardablePercentWAD(user)`, which computes the matured slot's contribution as `amountInCoolDown * 1e18 * coolDownInSecs / userTotalVlmgp / (T1 - T0)` - a value that keeps shrinking as `T1` grows, well past the point where the cooldown has already fully matured. [3](#0-2) 
5. The resulting lower `rewardablePercentWAD` causes a larger `forfeitAmount` to be deducted from the user's pending reward and redirected to `_queueNewRewardsWithoutTransfer`, permanently reducing what the user receives, with no way to reclaim the forfeited portion. [7](#0-6)

### Citations

**File:** VLMGP.sol (L193-218)
```text
    function getRewardablePercentWAD(address _user) override public view returns(uint256 percent) {
        uint256 fullyInLock = getUserTotalLocked(_user);
        uint256 inCoolDown = getUserAmountInCoolDown(_user);
        uint256 userTotalVlmgp = fullyInLock + inCoolDown;
        if (userTotalVlmgp == 0)
            return 0;
        percent = fullyInLock * 1e18 / userTotalVlmgp;

        uint256 timeNow = block.timestamp;
        UserUnlocking[] storage userUnlocking = userUnlockings[_user];

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

        return percent;
    }
```

**File:** VLMGP.sol (L292-306)
```text
        if (_slotIndex < getUserUnlockSlotLength(msg.sender)) {
            userUnlockings[msg.sender][_slotIndex] = UserUnlocking({
                startTime: block.timestamp,
                endTime: block.timestamp + coolDownInSecs,
                amountInCoolDown: _amountToCoolDown
            });
        } else {
            userUnlockings[msg.sender].push(
                UserUnlocking({
                    startTime: block.timestamp,
                    endTime: block.timestamp + coolDownInSecs,
                    amountInCoolDown: _amountToCoolDown
                })
            );
        }
```

**File:** rewards/vlMGPBaseRewarder.sol (L363-377)
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

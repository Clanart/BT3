### Title
Attacker can front-run their own reward claim with `cancelUnlock` to bypass cool-down reward forfeiture - ([File: rewards/vlMGPBaseRewarder.sol])

### Summary
`vlMGPBaseRewarder._calExpireForfeit` computes forfeiture using `vlMGP.getRewardablePercentWAD(_account)` sampled at claim time, not at the time reward accrual happened, while `VLMGP.cancelUnlock` lets a user instantly zero-out their cool-down amount without penalty. By calling `cancelUnlock(slotIndex)` immediately before triggering `getReward`, an attacker who accrued rewards while partially in cool-down can restore `getRewardablePercentWAD` to 100% right before the forfeit calculation runs, avoiding forfeiture entirely on rewards that accrued during the low-percent period.

### Finding Description
`_updateFor`/`updateReward(_account)` snapshots the user's *entire* pending reward delta (`_earned`) into `userRewards[token][account]` based on the **current** `balanceOf` weighting at claim time [1](#0-0) , and `_sendReward`/`_calExpireForfeit` then applies `vlMGP.getRewardablePercentWAD(_account)` — again read at **claim time** — to that entire accumulated amount to decide how much is forfeited [2](#0-1) . There is no historical/weighted tracking of the percent that was in effect while rewards were actually accruing; only the instantaneous state at claim time matters.

`VLMGP.cancelUnlock(_slotIndex)` is a public, unprivileged, `whenNotPaused`-gated function that simply zeroes `slot.amountInCoolDown` and decrements `totalAmountInCoolDown`, with no penalty and no forced reward settlement before the change [3](#0-2) . Since `getRewardablePercentWAD` computes `fullyInLock / (fullyInLock + inCoolDown)` plus a cool-down-weighted addition [4](#0-3) , removing the cool-down slot (`inCoolDown -> 0`) pushes `fullyInLock`'s share back toward 100% instantly.

Exploit flow:
1. Attacker holds vlMGP and has an open cool-down slot (`amountInCoolDown > 0`), during which `getRewardablePercentWAD` is depressed below 100%.
2. Rewards accrue (rewarder's `rewardPerTokenStored` increases) while the attacker is in this depressed-percent state.
3. In the same transaction/block, attacker calls `cancelUnlock(slotIndex)` — no cooldown-completion check is required (only that the slot is still in cool down, per `_checkInCoolDown`) — restoring `getRewardablePercentWAD(attacker)` to (near) 100%.
4. Attacker then triggers `getReward`/`getRewards` (via `MasterMagpie`, which the attacker can call as an unprivileged user through the normal claim path). `_updateFor` finalizes `userRewards` from the accrued `rewardPerToken` delta, and `_calExpireForfeit` reads the now-100% `getRewardablePercentWAD`, resulting in `forfeitAmount = 0` even though the reward accrued while the attacker was substantially in cool-down.

No existing modifier (`whenNotPaused`, `onlyMasterMagpie`, `updateReward`) prevents this because the forfeit computation is fundamentally based on point-in-time state rather than time-weighted/accrual-time state, and `cancelUnlock` is explicitly designed to be freely callable by the token owner at any time before the slot fully unlocks.

### Impact Explanation
This allows a user to steal reward yield that is meant to be redistributed to other lockers as "forfeited" rewards (via `_queueNewRewardsWithoutTransfer`, which redistributes forfeited amounts back into the reward pool for other stakers) [5](#0-4) . This matches the "theft or permanent freezing of unclaimed yield" impact class — the attacker gains reward share that should have gone to other lockers, and other lockers permanently lose that expected redistribution. The forfeiture mechanism's entire purpose (penalizing cool-down/unlocking users) is defeated for any attacker willing to pay `cancelUnlock` gas.

### Likelihood Explanation
Highly feasible and repeatable: no special capital is required beyond already holding vlMGP and having initiated a cool-down (a normal action), the attack requires only two ordinary transactions (`cancelUnlock` then `getReward`) that can be submitted back-to-back or in the same block by the same EOA, and there is no cooldown, fee, or restriction preventing `cancelUnlock` from being called repeatedly. An attacker can even intentionally start a cool-down, let rewards accrue, then use this technique every reward cycle, making this a systematically repeatable exploit rather than a one-off edge case.

### Recommendation
Base the forfeiture calculation on a time-weighted/rewardable percentage sampled at the time rewards accrued rather than the percentage at claim time — e.g., checkpoint `getRewardablePercentWAD` (or accumulate a rewardable-weighted `rewardPerToken`) whenever `rewardPerTokenStored` is updated or whenever the user's lock/cool-down state changes, and use that checkpointed value in `_calExpireForfeit` instead of a live call to `vlMGP.getRewardablePercentWAD(_account)`. Alternatively, force `_updateFor`/reward settlement to run (via `updateReward`) inside `cancelUnlock` before altering `amountInCoolDown`, ensuring rewards accrued during the depressed-percent period are settled at the correct forfeiture rate before the state change.

### Proof of Concept
Foundry test plan:
1. Deploy `VLMGP`, `vlMGPBaseRewarder`, and mock `MasterMagpie`; lock MGP for attacker and at least one other locker.
2. Attacker calls `startUnlock(amount)` to open a cool-down slot, dropping their `getRewardablePercentWAD` below 100% (assert via `vlMGP.getRewardablePercentWAD(attacker)`).
3. Queue new rewards into the rewarder (`_queueNewRewardsWithoutTransfer`/admin `queueNewRewards`) so `rewardPerTokenStored` increases while the attacker is in cool-down.
4. Record `expectedForfeit = calExpireForfeit(attacker, rewardToken)` at this point (pre-cancel) as the "should-have-been-forfeited" baseline.
5. Attacker calls `vlMGP.cancelUnlock(slotIndex)`.
6. Attacker calls `getReward` (through `MasterMagpie`) and records `claimedAmount`.
7. Assert `claimedAmount > (earnedAmount - expectedForfeit)`, i.e., the attacker received the full reward with `forfeitAmount == 0` despite having accrued rewards during a genuine cool-down period, demonstrating the theft of the other lockers' forfeited-reward entitlement.

### Citations

**File:** rewards/vlMGPBaseRewarder.sol (L331-347)
```text
    function _queueNewRewardsWithoutTransfer(uint256 _amountReward, address _rewardToken) internal
    {
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards = rewardInfo.historicalRewards + _amountReward;
        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**vlMGPDecimal) / totalStaked();
        }
        emit ForfeitRewardAdded(_amountReward, _rewardToken);
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L349-361)
```text
    function _updateFor(address _account) internal {
        uint256 length = rewardTokens.length;
        uint256 userVlMGPAmount = balanceOf(_account);

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;

            userRewards[rewardToken][_account] = _earned(_account, rewardToken, userVlMGPAmount);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
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

**File:** VLMGP.sol (L339-349)
```text
    function cancelUnlock(uint256 _slotIndex) external override whenNotPaused {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        _checkInCoolDown(msg.sender, _slotIndex);

        totalAmountInCoolDown -= slot.amountInCoolDown; // reduce amount to cool down accordingly
        slot.amountInCoolDown = 0; // not in cool down anymore

        emit ReLock(msg.sender, _slotIndex, slot.amountInCoolDown);
    }
```

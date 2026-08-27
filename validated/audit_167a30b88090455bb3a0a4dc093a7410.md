### Title
Cancelling an unlock slot (`cancelUnlock`) retroactively erases forfeiture on already-accrued rewards, letting attackers launder cooldown-period yield into full rewards - (File: `VLMGP.sol` / `rewards/vlMGPBaseRewarder.sol`)

### Summary
`vlMGPBaseRewarder._calExpireForfeit` computes the forfeitable portion of a user's *entire pending* reward balance using only the **current** snapshot of `vlMGP.getRewardablePercentWAD(_account)`, not a time-weighted history of how much of that reward accrued while the user's balance was actually in cooldown. Because `VLMGP.cancelUnlock()` is a fully permissionless, unprivileged call that instantly zeroes a cooldown slot's `amountInCoolDown` (converting it back into `fullyInLock`), an attacker can accrue rewards for a long time while partially in cooldown, then call `cancelUnlock` immediately before claiming to make `getRewardablePercentWAD` report ~100%, escaping the forfeiture that should have applied to the cooldown-period rewards.

### Finding Description
`getRewardablePercentWAD` (`VLMGP.sol:193-218`) computes the rewardable fraction from the account's *current* split between `fullyInLock` and `amountInCoolDown` slots: [1](#0-0) 

`vlMGPBaseRewarder._calExpireForfeit` applies this percent against the user's whole pending `userRewards` balance at the moment of `_sendReward`, not against the portion of reward accrued during each historical period: [2](#0-1) 

Reward accrual itself (`_earned`/`rewardPerToken`) is balance-based and lock-status-agnostic — a user's `balanceOf` (locked + cooldown) accrues `rewardPerToken` uniformly regardless of lock state: [3](#0-2) 

`cancelUnlock` is `external` with no access restriction beyond `whenNotPaused`, and simply zeroes `slot.amountInCoolDown` while it is still within the cooldown window, restoring the amount to `fullyInLock` (since `getUserTotalLocked` is derived as `stakedInMasterMagpie - amountInCoolDown`) — it does **not** trigger `_updateFor`/reward crystallization on the rewarder, and more importantly does not perform any partial/time-weighted forfeiture of the rewards that already accrued while the funds sat in cooldown: [4](#0-3) 

Exploit flow:
1. Attacker locks MGP and calls `startUnlock` to move part of the balance into a cooldown slot.
2. Reward drops (`queueNewRewards`/`queueMGP`) accrue `rewardPerTokenStored` over time while the attacker's balance is split between `fullyInLock` and `amountInCoolDown`. Under correct design, the cooldown portion should be partially forfeited when claimed, per `getRewardablePercentWAD`.
3. Right before claiming, attacker calls `cancelUnlock(slotIndex)` — a fully unprivileged, permissionless call — converting the cooldown slot back into `fullyInLock` instantly.
4. Attacker calls `claim`/`multiclaimFor` (routed to `getReward`/`getRewards`, `onlyMasterMagpie`-gated but reachable by any user's own claim call). `_updateFor` computes `userRewards` using the *full* accrued `rewardPerToken` delta (all of it, since `_earned` doesn't distinguish lock state), then `_sendReward` calls `_calExpireForfeit`, which now sees `getRewardablePercentWAD` ≈ 100% because the cooldown slot no longer exists.
5. Attacker receives the full reward with (near) zero forfeiture, even though a portion of it accrued while their balance was locked in cooldown and should have been partially forfeited.

Existing checks do not stop this: there is no `nonReentrant`/timing guard preventing `cancelUnlock` immediately before `claim` in the same transaction, and no historical/time-weighted accounting ties forfeiture to the period during which the reward actually accrued.

### Impact Explanation
This is a theft of yield that should have been forfeited back into the reward pool (via `_queueNewRewardsWithoutTransfer`) for other stakers. The attacker can launder the entire forfeitable amount for any account they control, at will, before every claim — a direct, repeatable diversion of protocol yield intended for socialization to remaining stakers. This matches "theft or permanent freezing of unclaimed yield."

### Likelihood Explanation
Fully unprivileged and cheap: any EOA can lock MGP, start a cooldown slot, wait for reward drops (which happen regularly via `queueNewRewards`/`queueMGP`), then atomically call `cancelUnlock` followed by `claim` in the same transaction or block. No special capital beyond the locked MGP itself is required, and the exploit is repeatable every time reward accrues while any portion of the user's balance is in cooldown.

### Recommendation
Crystallize (and apply forfeiture to) pending rewards for the affected cooldown slot's proportional share *before* mutating `amountInCoolDown`/lock status in `cancelUnlock` (and similarly in `unlock`/`forceUnLock`), i.e., call `_updateFor`+apply `_calExpireForfeit` snapshot at that point, or track forfeiture on a per-period, time-weighted basis rather than solely on the state at final claim time.

### Proof of Concept
Foundry test plan:
1. Deploy `VLMGP`, `vlMGPBaseRewarder`, `MasterMagpie` per existing test harness.
2. Attacker locks `X` MGP, then `startUnlock(Y)` (Y < X) creating a cooldown slot.
3. Advance time partway through cooldown; manager calls `queueNewRewards`/`queueMGP` with a large reward amount, increasing `rewardPerTokenStored`.
4. Compute expected `forfeitAmount` via `calExpireForfeit` at this point (based on current `getRewardablePercentWAD`, which is `< 100%` due to the active cooldown slot) — this is the "baseline" expected forfeiture had claim happened now.
5. Attacker calls `cancelUnlock(slotIndex)`.
6. Attacker calls `claim`/`multiclaimFor` in the same transaction.
7. Assert: actual `forfeitAmount` observed via `ForfeitRewardAdded`/`RewardPaid` events is near zero, while the baseline computed in step 4 was significantly greater than zero — proving the attacker escaped forfeiture that should have applied to rewards accrued during the cooldown period. [5](#0-4)

### Citations

**File:** VLMGP.sol (L193-199)
```text
    function getRewardablePercentWAD(address _user) override public view returns(uint256 percent) {
        uint256 fullyInLock = getUserTotalLocked(_user);
        uint256 inCoolDown = getUserAmountInCoolDown(_user);
        uint256 userTotalVlmgp = fullyInLock + inCoolDown;
        if (userTotalVlmgp == 0)
            return 0;
        percent = fullyInLock * 1e18 / userTotalVlmgp;
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

**File:** rewards/vlMGPBaseRewarder.sol (L379-384)
```text
    function _earned(address _account, address _rewardToken, uint256 _userVlmgpShare) internal view returns (uint256) {
        return ((_userVlmgpShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**vlMGPDecimal) + userRewards[_rewardToken][_account];
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

### Title
Forfeiture bypass via `VLMGP.cancelUnlock` front-running `vlMGPBaseRewarder.getReward` allows a lock-cooldown user to escape yield forfeiture - (File: `VLMGP.sol`, `rewards/vlMGPBaseRewarder.sol`)

### Summary
`_calExpireForfeit` computes the forfeited portion of accrued rewards using `vlMGP.getRewardablePercentWAD(_account)` evaluated **at the moment of harvest**, not at the moment the rewards accrued. Because `cancelUnlock` lets a user instantly exit an active cooldown slot with no penalty and no waiting period, a user can accrue rewards while nominally in cooldown (a discounted percent), then call `cancelUnlock` immediately before `getReward`/`multiclaimFor` to restore `getRewardablePercentWAD` to 100%, zeroing `forfeitAmount` on rewards that should have been partially forfeited.

### Finding Description
`VLMGP.getRewardablePercentWAD` blends a user's "fully locked" and "in cooldown" balances to derive a reward-eligibility percentage: [1](#0-0) 

`cancelUnlock` unconditionally zeroes `slot.amountInCoolDown` and decrements `totalAmountInCoolDown`, with only an in-cooldown check and no time-lock or penalty: [2](#0-1) 

`vlMGPBaseRewarder._sendReward` calls `_calExpireForfeit`, which reads `vlMGP.getRewardablePercentWAD(_account)` live, at claim time, and applies it to the *entire* accumulated `userRewards` balance regardless of how much of that balance accrued while the user was actually in cooldown: [3](#0-2) 

Exploit flow:
1. User calls `startUnlock(_amountToCoolDown)`, placing part of their vlMGP balance into a cooldown slot; this reduces `getUserTotalLocked` and increases `getUserAmountInCoolDown`, lowering `getRewardablePercentWAD` for the duration of the cooldown.
2. Rewards accrue over time (via `rewardPerToken` growth) while the user's `getRewardablePercentWAD` is below 100%, meaning a forfeiture would normally apply on harvest.
3. Immediately before calling `getReward`/`multiclaimFor` (same block, e.g. via a `multicall`-style batched transaction, or simply as the prior transaction in the same block), the user calls `cancelUnlock(_slotIndex)`. This sets `amountInCoolDown = 0`, so `getRewardablePercentWAD` recomputes to (near) 100% because there is no cooldown balance left to discount.
4. `getReward` then computes `_calExpireForfeit` using this now-100% percent, so `forfeitAmount = 0` even though the rewards accrued while the user was in cooldown.
5. Nothing prevents the user from immediately calling `startUnlock` again afterward to resume unlocking, repeating this pattern for every harvest.

No modifier (`whenNotPaused`, absence of `nonReentrant` on `cancelUnlock`) or accounting check prevents this because the forfeiture logic has no memory of "time spent in cooldown while these specific rewards accrued" — it is a spot-check at claim time, defeated by toggling cooldown state around the claim.

### Impact Explanation
This bypasses the intended forfeiture mechanism whose purpose is to discourage passive holders and redistribute forfeited yield (`_queueNewRewardsWithoutTransfer`) to remaining/fully-locked stakers via `rewardPerTokenStored`. By evading forfeiture, the attacker retains yield that should have been redirected to other locked users, directly reducing the redistribution pool — matching "theft of unclaimed yield" that should have been forfeited to other participants. This is a repeatable, ongoing yield-siphoning mechanism rather than a one-time bug.

### Likelihood Explanation
No special privileges, capital, or complex conditions are required — only an active vlMGP lock and normal unlock/cooldown participation, both standard user actions. `cancelUnlock` has no cooldown, penalty, or timelock restricting its use, and can be executed in the same block/transaction bundle as the reward claim (e.g., via `multiclaimFor`/multicall patterns already used elsewhere in the contract, such as inside `startUnlock`/`unlock` themselves). This makes the exploit trivially and repeatedly executable by any vlMGP holder who wants to unlock at some point but still avoid forfeiture.

### Recommendation
Compute/lock in the forfeiture percentage (or snapshot the forfeitable amount) at the time rewards accrue per reward-index update (in `_updateFor`/`updateRewards`), rather than recomputing `getRewardablePercentWAD` fresh at `_sendReward` time. Alternatively, apply a minimum lock/cooldown period on `cancelUnlock` (e.g., disallow canceling and re-locking within the same block/epoch as a pending claim, or apply a partial penalty to canceled cooldowns similar to `forceUnLock`) so that toggling cooldown state cannot be used purely to reset the reward-eligibility percentage right before harvesting.

### Proof of Concept
Foundry test outline:
1. Deploy `VLMGP`, `vlMGPBaseRewarder`, and mock `MGP`/`masterMagpie` per existing test harness.
2. User locks MGP via `lock()`, then calls `startUnlock(amount)` to open a cooldown slot.
3. Advance time partway through `coolDownInSecs` and queue reward tokens into the rewarder (`_queueNewRewardsWithoutTransfer` equivalent / `queueNewRewards`) so `userRewards` accrues while `getRewardablePercentWAD(user) < 1e18`.
4. Assert `vlMGPBaseRewarder.calExpireForfeit(user, rewardToken) > 0` (baseline: forfeiture would apply if harvested now while still in cooldown).
5. In the same test transaction/block, call `VLMGP.cancelUnlock(slotIndex)` immediately followed by `vlMGPBaseRewarder.getReward(user, ...)` (or `masterMagpie.multiclaimFor`).
6. Assert the actual `forfeitAmount` applied during the harvest (via emitted `MGPHarvested`/`ForfeitRewardAdded` events or reward balance delta) is `0`, contradicting the non-zero value computed in step 4 — demonstrating the forfeiture bypass.

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

**File:** rewards/vlMGPBaseRewarder.sol (L363-400)
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

    function _earned(address _account, address _rewardToken, uint256 _userVlmgpShare) internal view returns (uint256) {
        return ((_userVlmgpShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**vlMGPDecimal) + userRewards[_rewardToken][_account];
    }

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

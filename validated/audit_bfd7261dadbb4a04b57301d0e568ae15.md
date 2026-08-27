### Title
`mWOMSVBaseRewarder._calExpireForfeit` never applies an early-unlock reward forfeit, letting unlocking mWOMSV holders keep 100% of pro-rata rewards that should be redirected to loyal lockers - ([File: rewards/mWOMSVBaseRewarder.sol])

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` is a stub that always returns `forfeitAmount == 0` because it sets `rewardableAmount = _amount` unconditionally instead of consulting `mWomSV.getRewardablePercentWAD(_account)` the way the sibling `vlMGPBaseRewarder._calExpireForfeit` consults `vlMGP.getRewardablePercentWAD(_account)`. As a result, a user who starts (or completes) an unlock keeps their full share of any reward queued while they hold reduced "loyalty," even though `mWomSV` already tracks exactly the data needed (`getRewardablePercentWAD`) to compute the intended penalty.

### Finding Description
`mWOMSVBaseRewarder.balanceOf` reads the user's staked amount from `IMasterMagpie.stakingInfo`, which is only reduced when `mWomSV._unlock` calls `IMasterMagpie.withdrawMWomSVFor` at final `unlock()`, not at `startUnlock()`. [1](#0-0)  So while a slot is in cool down, the user's `balanceOf` in the rewarder is unchanged and they keep accruing full pro-rata `rewardPerTokenStored` via `_earned`. [2](#0-1) 

When the reward is actually paid out in `_sendReward`, the code is supposed to forfeit a portion proportional to how "unlocked"/disloyal the staker currently is and re-queue that forfeited portion to remaining stakers via `_queueNewRewardsWithoutTransfer`: [3](#0-2) 

But `_calExpireForfeit` in this contract never reduces `rewardableAmount`:
```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardableAmount = _amount;
    if (rewardableAmount > _amount)
        revert InvalidRewardableAmount();
    uint256 forfeitAmount = _amount - rewardableAmount;
    ...
    return forfeitAmount;
}
``` [4](#0-3) 

This is unconditionally `0` for any `_account`/`_amount` - `rewardableAmount` is set to `_amount` with no lookup of lock status. Compare this to `vlMGPBaseRewarder._calExpireForfeit`, which actually queries the lock contract:
```solidity
uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
``` [5](#0-4) 

`mWomSV` itself already implements the equivalent function that the rewarder should be calling but isn't:
```solidity
function getRewardablePercentWAD(address _user) override public view returns(uint256 percent) {
    uint256 fullyInLock = getUserTotalLocked(_user);
    uint256 inCoolDown = getUserAmountInCoolDown(_user);
    ...
}
``` [6](#0-5) 

This confirms design intent parity with the vlMGP path (down to duplicated struct/state layout in `mWOMSVBaseRewarder.sol` and `vlMGPBaseRewarder.sol`) that was left disconnected in the mWOMSV variant. None of `onlyMasterMagpie`, `updateReward`, or `nonReentrant` modifiers prevent this - they only gate access/ordering, not the forfeit math itself. [7](#0-6) 

Exploit flow:
1. Attacker (unprivileged, holds mWOMSV via normal `lock`) calls `startUnlock(fullAmount)`. This does not reduce `IMasterMagpie.stakingInfo` staked balance (only `withdrawMWomSVFor` in final `unlock()` does), so `balanceOf` in the rewarder stays unchanged. [8](#0-7) 
2. `rewardManager` (WombatStaking, a normal privileged-but-external caller of the protocol's ordinary flow, not attacker-controlled) calls `queueNewRewards(largeAmount, token)`, which increases `rewardPerTokenStored` proportional to `totalStaked()`, crediting the attacker their full pro-rata share despite them being mid-unlock. [9](#0-8) 
3. Attacker calls `getReward()`/`getRewards()` before finalizing `unlock()`. `_sendReward` computes `forfeitAmount = _calExpireForfeit(...)` which is always `0`, so `toSend == full earned amount`, and no forfeited/loyalty-preserved amount is fed back to `_queueNewRewardsWithoutTransfer` for the remaining, non-unlocking lockers. [3](#0-2) 

### Impact Explanation
This falls under "theft of unclaimed yield that should be redirected to remaining stakers." The intended mechanism (mirrored exactly in `vlMGPBaseRewarder`) is that a fraction of a reward tied to an account's declining loyalty percentage should be redirected back into the pool for stakers who keep their position locked. Because `mWOMSVBaseRewarder` never computes a non-zero forfeit, every unlocking/exiting mWOMSV holder captures 100% of rewards accrued for the entire measurement window even while economically exiting the lock, permanently denying that yield to loyal remaining lockers who were supposed to receive it via `_queueNewRewardsWithoutTransfer`.

### Likelihood Explanation
Preconditions are minimal and fully attacker-controlled: any address holding mWOMSV (obtained by depositing/locking mWOM, a normal permissionless action) can call `startUnlock` and then `getReward`/`getRewards` at any time, with no special role required. The `queueNewRewards` call is a normal, expected operational action by `rewardManager` (WombatStaking) that happens routinely, not an attacker-triggered event, so the attacker only needs to time their `startUnlock`+harvest around it. This is fully repeatable on every reward distribution cycle for as long as the bug exists.

### Recommendation
Fix `mWOMSVBaseRewarder._calExpireForfeit` to mirror `vlMGPBaseRewarder`'s implementation by querying `mWomSV.getRewardablePercentWAD(_account)` and scaling `rewardableAmount` accordingly, e.g.:
```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardablePercentWAD = mWOMSV.getRewardablePercentWAD(_account);
    uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
    ...
}
```

### Proof of Concept
Foundry test plan:
1. Deploy `mWomSV`, `MasterMagpie`, `mWOMSVBaseRewarder`, set `WombatStaking` (mocked) as `rewardManager`/manager.
2. Two users (Attacker, Loyal) each `lock` equal `mWOM` amounts; verify equal `balanceOf` in the rewarder.
3. Attacker calls `mWomSV.startUnlock(fullAmount)`.
4. `rewardManager` calls `mWOMSVBaseRewarder.queueNewRewards(largeAmount, rewardToken)`.
5. Attacker calls `getReward()` via MasterMagpie (as `onlyMasterMagpie`), before finishing `unlock()`.
6. Assert: `toSend == earned(attacker, rewardToken)` (full share) and emitted `RewardPaid` amount has no corresponding `ForfeitRewardAdded` event (`forfeitAmount == 0`).
7. Repeat identical state/timing on `vlMGPBaseRewarder` with `VLMGP` (equivalent unlock action) and assert `calExpireForfeit` returns non-zero and `ForfeitRewardAdded` is emitted, demonstrating the mWOMSV path is the outlier lacking the intended penalty/redistribution.

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L146-149)
```text
    function balanceOf(address _account) public override view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account);
        return staked;
    }
```

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

**File:** rewards/mWOMSVBaseRewarder.sol (L305-328)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20Metadata(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
        );
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;

        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**mWOMSVDecimal) / totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
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

**File:** rewards/mWOMSVBaseRewarder.sol (L378-383)
```text
    function _earned(address _account, address _rewardToken, uint256 _userMWOMSVShare) internal view returns (uint256) {
        return ((_userMWOMSVShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**mWOMSVDecimal) + userRewards[_rewardToken][_account];
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

**File:** wombat/mWomSV.sol (L181-206)
```text
    function getRewardablePercentWAD(address _user) override public view returns(uint256 percent) {
        uint256 fullyInLock = getUserTotalLocked(_user);
        uint256 inCoolDown = getUserAmountInCoolDown(_user);
        uint256 userTotalmWomSV = fullyInLock + inCoolDown;
        if (userTotalmWomSV == 0)
            return 0;
        percent = fullyInLock * 1e18 / userTotalmWomSV;

        uint256 timeNow = block.timestamp;
        UserUnlocking[] storage userUnlocking = userUnlockings[_user];

        for (uint256 i; i < userUnlocking.length; i++) {
            if (userUnlocking[i].amountInCoolDown > 0) {
                if (block.timestamp > userUnlocking[i].endTime) {// fully unlocked 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 * (userUnlocking[i].endTime - userUnlocking[i].startTime)
                        / userTotalmWomSV / (timeNow - userUnlocking[i].startTime);
                }
                else {// still in cool down 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 / userTotalmWomSV;
                }

            }
        }

        return percent;
    }
```

**File:** wombat/mWomSV.sol (L247-277)
```text
    function startUnlock(uint256 _amountToCoolDown) external override whenNotPaused nonReentrant {
        if (_amountToCoolDown > getUserTotalLocked(msg.sender))
            revert NotEnoughLockedMWOM();

        uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
        address[] memory lps = new address[](1);
        address[][] memory mWomSVrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, mWomSVrewards, msg.sender);

        uint256 _slotIndex = getNextAvailableUnlockSlot(msg.sender);
        totalAmountInCoolDown += _amountToCoolDown;

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

        emit UnlockStarts(msg.sender, block.timestamp, _amountToCoolDown);
    }
```

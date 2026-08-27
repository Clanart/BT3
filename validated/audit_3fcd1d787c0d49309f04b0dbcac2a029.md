### Title
Broken expiry/forfeit check in `mWOMSVBaseRewarder` allows unlocking users to claim full rewards instead of forfeiting the unvested portion - (File: `rewards/mWOMSVBaseRewarder.sol`)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` never actually computes a forfeiture: it self-assigns `rewardableAmount = _amount`, guaranteeing `forfeitAmount = 0` regardless of a user's lock/unlock state. This differs from the equivalent `vlMGPBaseRewarder._calExpireForfeit`, which correctly derives `rewardableAmount` from `vlMGP.getRewardablePercentWAD(_account)` — a percentage that decays for tokens that are mid-cooldown/unlocking. As a result, users who have started unlocking their `mWomSV` (partially exited their lock commitment) still receive 100% of the pool's distributed rewards instead of only their "rewardable" (still-locked) share, silently draining reward funds that should be forfeited back into the pool for remaining long-term lockers.

### Finding Description
`mWomSV` exposes `getRewardablePercentWAD(address _user)` [1](#0-0)  which computes a time/weight decayed percentage of a user's balance that should be considered "rewardable" based on how much of their `mWomSV` is still fully locked versus in cool-down/unlocking.

The correct consumer pattern is implemented in `vlMGPBaseRewarder._calExpireForfeit`, which multiplies the pending reward by this rewardable percentage to determine the forfeited (non-rewardable) portion: [2](#0-1) 

However, the mWomSV-specific rewarder, `mWOMSVBaseRewarder._calExpireForfeit`, does not call `mWOMSV.getRewardablePercentWAD` at all. Instead it sets `rewardableAmount = _amount` directly, making the forfeit calculation dead code that always evaluates to zero: [3](#0-2) 

This function is invoked from `_sendReward`, which is called on every `getReward`/`getRewards` claim path (reachable by any ordinary staker through `MasterMagpie`): [4](#0-3) 

The contract even declares `ILocker public mWOMSV;` [5](#0-4)  — the interface exposing `getRewardablePercentWAD` — but never uses it for forfeiture, confirming the check was intended but not wired up, analogous to the reported "no expiration check" bug class where an entitlement/eligibility check that should gate reward participation is missing.

### Impact Explanation
Users who initiate `startUnlock`/enter cool-down on their `mWom` lock (via `mWomSV.startUnlock`) remain counted in `balanceOf`/`totalStaked` of `mWOMSVBaseRewarder` (since `MasterMagpie.stakingInfo` still counts amounts in cool down) [6](#0-5) , yet because the forfeiture math is broken, they can claim their entire accrued reward share with zero forfeiture, instead of only the fraction that `getRewardablePercentWAD` would have allowed. The forfeited amount that should be re-queued to `rewards[...]` via `_queueNewRewardsWithoutTransfer` and redistributed to remaining, fully-committed lockers never materializes, permanently diverting yield away from long-term stakers to users who are actively exiting their commitment — a direct theft/permanent loss of unclaimed yield for the remaining pool participants.

### Likelihood Explanation
The flawed function executes unconditionally on every single reward claim (`getReward`/`getRewards`) for any account holding `mWomSV`, with no special privilege required — an ordinary user only needs to call `startUnlock` and then claim rewards through `MasterMagpie`/`mWOMSVBaseRewarder.getReward`. This makes the bug trivially and continuously triggerable by any unprivileged wallet.

### Recommendation
Fix `mWOMSVBaseRewarder._calExpireForfeit` to mirror `vlMGPBaseRewarder`'s implementation by computing `rewardableAmount` from `mWOMSV.getRewardablePercentWAD(_account)` instead of self-assigning `_amount`, ensuring the forfeited portion of rewards for unlocking users is correctly withheld and re-queued for remaining stakers.

### Proof of Concept
1. User locks `mWom` via `mWomSV.lock`, accruing rewards in the `mWOMSVBaseRewarder` pool over time.
2. User calls `mWomSV.startUnlock(amount)` to place part of their balance into cool-down (still counted by `balanceOf`/`totalStaked`) [7](#0-6) .
3. User calls `getReward` (via `MasterMagpie`) which routes to `mWOMSVBaseRewarder._sendReward` -> `_calExpireForfeit` [8](#0-7) .
4. Because `_calExpireForfeit` sets `rewardableAmount = _amount`, `forfeitAmount` is always `0`; the user receives the full `userRewards[...]` amount with no penalty for having begun unlocking, unlike the intended design mirrored in `vlMGPBaseRewarder`.

### Citations

**File:** wombat/mWomSV.sol (L101-103)
```text
    function balanceOf(address _user) public override view returns (uint256) {
        return getUserTotalLocked(_user) + getUserAmountInCoolDown(_user);
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

**File:** rewards/mWOMSVBaseRewarder.sol (L22-22)
```text
    ILocker public mWOMSV; 
```

**File:** rewards/mWOMSVBaseRewarder.sol (L362-398)
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

    function _earned(address _account, address _rewardToken, uint256 _userMWOMSVShare) internal view returns (uint256) {
        return ((_userMWOMSVShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**mWOMSVDecimal) + userRewards[_rewardToken][_account];
    }

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

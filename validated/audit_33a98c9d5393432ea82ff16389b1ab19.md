### Title
MasterMagpie MGP emissions continue to accrue and remain claimable on `vlMGP`/`mWomSV` positions during the unlock cooldown, contrary to the forfeit design - (File: rewards/MasterMagpie.sol, VLMGP.sol)

### Summary
When a `vlMGP` (or `mWomSV`) holder calls `startUnlock`, their MGP tokens remain staked in `MasterMagpie`'s accounting — `UserInfo.amount` is never reduced — so the user keeps earning full-rate `MasterMagpie` MGP emissions during the entire cooldown period, even though the protocol's forfeit design says a cooling-down position should progressively lose reward eligibility.

### Finding Description
`VLMGP.startUnlock` records the amount going into cooldown in a separate `userUnlockings` mapping but does **not** withdraw or reduce the corresponding stake inside `MasterMagpie`: [1](#0-0) 

`MasterMagpie.stakingInfo` (and therefore `BaseRewardPool.balanceOf` / `BaseRewardPoolV2.balanceOf` / `vlMGPBaseRewarder.balanceOf`) simply returns the raw `userInfo[_stakingToken][_user].amount`, with no knowledge of cooldown at all: [2](#0-1) [3](#0-2) [4](#0-3) 

The protocol's own `VLMGP.getUserTotalLocked` explicitly excludes the cooldown amount for the purposes of "locked" accounting: `_lockAmount = _amountInMasterMagpie - getUserAmountInCoolDown(_user)`, confirming that the cooling-down portion is intended to be treated differently from an actively-locked position: [5](#0-4) 

Likewise `getRewardablePercentWAD` is specifically designed to compute a reduced "rewardable" share for the portion of a user's balance sitting in cooldown, and `vlMGPBaseRewarder._calExpireForfeit` uses this percentage to forfeit part of the *bonus* reward tokens paid out through the rewarder at claim time: [6](#0-5) [7](#0-6) 

However, this forfeit logic only applies to the secondary/bonus reward token(s) distributed through `vlMGPBaseRewarder`. The primary MGP emission stream computed and paid out by `MasterMagpie` itself (`_calMGPReward`, driven by `userInfo[_stakingToken][_user].amount` and `pool.accMGPPerShare`) has no equivalent forfeit or cooldown check anywhere in `MasterMagpie.sol`. Since `startUnlock` never reduces `userInfo.amount`, the cooling-down MGP continues to accrue MGP emissions at the full, un-penalized rate for the entire cooldown period, and the user can freely harvest those MGP rewards via `multiclaimFor`/harvest while the tokens sit in `userUnlockings`.

This mirrors the reported Alchemix `RevenueHandler.claim` bug class: a lock/cooldown mechanism exists and is documented/implemented as reducing reward eligibility, but one of the two reward-distribution code paths (here, `MasterMagpie`'s core MGP emission, versus Alchemix's `claim`) fails to enforce it, letting users keep earning/claiming yield they should have forfeited by entering cooldown.

### Impact Explanation
Users who start unlocking (cooldown) their `vlMGP`/`mWomSV` position continue to receive full MGP emission rewards from `MasterMagpie` for the entire cooldown duration, when the intended behavior (as implemented for the bonus-reward path via `getRewardablePercentWAD`/`_calExpireForfeit`) is to forfeit a growing portion of rewards while a position is cooling down. This results in theft of unclaimed yield — value that should be redistributed/forfeited to non-cooling stakers is instead paid out to the cooling-down user, at the expense of protocol reward economics and other stakers whose queued/forfeited rewards depend on this mechanism (`_queueNewRewardsWithoutTransfer`).

### Likelihood Explanation
High likelihood: any ordinary `vlMGP`/`mWomSV` holder can trigger this simply by calling `startUnlock` and then normally interacting with `MasterMagpie` to harvest MGP rewards — no privileged role, governance action, or special conditions are required, and the cooldown duration (`coolDownInSecs`) provides an extended window (well over 24 hours in typical protocol configuration) during which the exploit persists.

### Recommendation
Either (a) have `VLMGP.startUnlock`/`mWomSV` equivalent actually reduce the user's staked `amount` in `MasterMagpie` for the portion moved into cooldown (transferring it into a separate non-rewarding tracking bucket), or (b) apply the same `getRewardablePercentWAD` forfeit calculation to the core MGP emission computed in `MasterMagpie._calMGPReward`/harvest path, consistent with how it is already applied to bonus rewards in `vlMGPBaseRewarder._calExpireForfeit`.

### Proof of Concept
1. User locks MGP via `VLMGP.lock`, staking is recorded in `MasterMagpie.userInfo[vlMGP][user].amount`.
2. User calls `VLMGP.startUnlock(amount)` — `userUnlockings` cooldown slot is created, but `MasterMagpie`'s `userInfo.amount` is untouched: [1](#0-0) 
3. Time passes (still within cooldown, `slot.endTime > block.timestamp`).
4. User calls `MasterMagpie`'s harvest/`multiclaimFor` for the `vlMGP` pool — MGP emissions computed from `_calMGPReward` (based on the still-unchanged `userInfo.amount`) are paid in full, with no reduction despite the position being in cooldown, while `VLMGP.getUserTotalLocked` for the very same account correctly reports a reduced "locked" balance excluding the cooldown amount, showing the inconsistency between the two reward paths: [5](#0-4)

### Citations

**File:** VLMGP.sol (L122-129)
```text
    /// @notice Get the total MGP a user locked, not counting the ones in cool down
    /// @param _user the user
    /// @return _lockAmount the total MGP a user locked, not counting the ones in cool down
    function getUserTotalLocked(address _user) override public view returns (uint256 _lockAmount) {
        // needs fixing
        (uint256 _amountInMasterMagpie, ) = IMasterMagpie(masterMagpie).stakingInfo(address(this), _user);
        _lockAmount = _amountInMasterMagpie - getUserAmountInCoolDown(_user);
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

**File:** VLMGP.sol (L275-311)
```text
    function startUnlock(uint256 _amountToCoolDown) external override whenNotPaused nonReentrant {
        if (_amountToCoolDown > getUserTotalLocked(msg.sender))
            revert NotEnoughLockedMPG();

        uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
        if (address(wombatBribeManager) != address(0) && 
            totalLockAfterStartUnlock < IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender))
            revert NotEnoughLockedMPG();

        address[] memory lps = new address[](1);
        address[][] memory vlMGPrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender);

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

        if (referralStorage != address(0)) IReferralStorage(referralStorage).updateTotalFactor(msg.sender);

        emit UnlockStarts(msg.sender, block.timestamp, _amountToCoolDown);
    }
```

**File:** rewards/MasterMagpie.sol (L260-266)
```text
    function stakingInfo(address _stakingToken, address _user)
        public
        view
        returns (uint256 stakedAmount, uint256 availableAmount)
    {
        return (userInfo[_stakingToken][_user].amount, userInfo[_stakingToken][_user].available);
    }
```

**File:** rewards/BaseRewardPool.sol (L130-136)
```text
    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L141-148)
```text
    /// @notice Returns lock weighting of an user. Lock weighting is calculated by 
    /// amount of MGP still in lock + amount of MGP in cool down / 2
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account);
        return staked;
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

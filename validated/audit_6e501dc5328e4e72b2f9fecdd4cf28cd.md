## Analysis

The Trail-of-Bits report's root cause is a **manually duplicated/hand-copied piece of logic that silently loses a critical check**, producing divergent behavior between the "intended" and "actual" deployed code. The closest analog in `blackvul/contracts--006` is in `rewards/mWOMSVBaseRewarder.sol`, where the forfeit-calculation logic that should compute a partial "rewardable" share (mirroring the `getRewardablePercentWAD` pattern used in the sibling `VLMGP`/`mWomSV` lockers) was left as dead/placeholder code, permanently disabling the forfeiture mechanism the contract's name and events (`ForfeitRewardAdded`, `InvalidRewardableAmount`) indicate it was designed to have. [1](#0-0) 

Compare with the twin locker `VLMGP.sol`, which does implement an actual expiry/penalty computation as part of the same "lock slot / forfeit" pattern family: [2](#0-1) 

And `mWomSV.sol`'s `getRewardablePercentWAD`, which computes a genuine time-weighted rewardable percentage that a forfeit calculation should have been derived from: [3](#0-2) 

### Title
Dead/placeholder forfeit-calculation logic in `mWOMSVBaseRewarder._calExpireForfeit` permanently disables reward forfeiture, misallocating locked-holder yield - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` sets `rewardableAmount = _amount` and then checks `if (rewardableAmount > _amount)`, a condition that can never be true, resulting in `forfeitAmount` always equal to `0`. [1](#0-0) 

### Finding Description
`_sendReward` is meant to withhold a forfeited portion of rewards from users who are not fully-locked (e.g., users mid-cooldown/unlock) and requeue that forfeited amount for redistribution to fully-locked stakers via `_queueNewRewardsWithoutTransfer`: [4](#0-3) 

The forfeit percentage is expected to be derived from the user's "rewardable" (fully-locked vs. cooldown) proportion — exactly the value `getRewardablePercentWAD` computes in the sibling locker contracts `mWomSV.sol`/`VLMGP.sol`. However, in `mWOMSVBaseRewarder._calExpireForfeit`, this computation was never wired in; `rewardableAmount` is hardcoded to equal `_amount`, making `forfeitAmount` always `0` regardless of the account's lock/cooldown state. This is the same class of bug flagged in the reference report: logic that must be manually kept in sync across duplicated/parallel contract copies (here, the `VLMGP`/`mWomSV` "lock slot" family vs. its reward-forfeit counterpart) diverged, and the security-critical check (forfeiture based on rewardable percent) was dropped.

### Impact Explanation
Because forfeiture never triggers, any unprivileged holder of `mWOMSV` can lock, immediately begin unlocking (entering cooldown or having already withdrawn), and still claim 100% of `mWOMSVBaseRewarder` rewards as if fully locked, with `_earned`/`_calExpireForfeit` never reducing their claim: [5](#0-4) 

This permanently prevents the redistribution of forfeited yield to loyal, fully-locked stakers — the yield that should flow to them via `_queueNewRewardsWithoutTransfer` never materializes, since `forfeitAmount` is always zero. This is a permanent (not time-bound) misallocation/freezing of unclaimed yield that should have accrued to fully-locked participants.

### Likelihood Explanation
High likelihood: this path is triggered on every normal user claim (`_sendReward`/`_calExpireForfeit`/`calExpireForfeit`) by any ordinary wallet holding `mWOMSV`, requires no privileged role, and is deterministic — the dead condition (`rewardableAmount > _amount`, where `rewardableAmount == _amount`) is unconditionally false on every call.

### Recommendation
Wire `_calExpireForfeit` to actually compute the user's rewardable share (e.g., via `mWOMSV.getRewardablePercentWAD(_account)` as used in the sibling locker contracts) rather than a hardcoded pass-through, and add tests asserting forfeited amounts are non-zero for accounts with active cooldown/unlock slots. More broadly, since this logic is duplicated conceptually across `VLMGP`, `mWomSV`, and `mWOMSVBaseRewarder`, consider extracting the rewardable/forfeit percentage computation into a single shared library so it cannot silently diverge across contracts.

### Proof of Concept
1. User locks `mWOM` into `mWomSV`, which stakes into `MasterMagpie` and starts accruing rewards in `mWOMSVBaseRewarder`.
2. User calls `startUnlock` in `mWomSV.sol` to begin cooldown on their full balance (this only reduces `getUserTotalLocked`, not their reward eligibility in the rewarder). [6](#0-5) 

3. User calls `getReward`/claim path in `mWOMSVBaseRewarder`, which routes through `_sendReward` → `_calExpireForfeit(_account, userRewards[...])`.
4. Because `rewardableAmount = _amount` unconditionally, `forfeitAmount` computed is always `0`, and `toSend = userRewards[...] - 0` — the user receives full rewards despite being in cooldown, and no forfeited amount is ever re-queued for fully-locked stakers. [7](#0-6)

### Citations

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

**File:** VLMGP.sol (L234-248)
```text
    function expectedPenaltyAmount(uint256 _slotIndex) public view returns(uint256 penaltyAmount, uint256 amontToUser) {
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        uint256 coolDownAmount = slot.amountInCoolDown;
        uint256 baseAmountToUser = slot.amountInCoolDown / 5;
        uint256 waitingAmount = coolDownAmount - baseAmountToUser;

        uint256 unlockFactor = 1e12;
        if((block.timestamp - slot.startTime) <= (slot.endTime - slot.startTime))
            unlockFactor = ((block.timestamp - slot.startTime) * 1e12 / (slot.endTime - slot.startTime)) ** 2 / 1e12;

        uint256 unlockAmount = waitingAmount * unlockFactor / 1e12;
        amontToUser = baseAmountToUser + unlockAmount;
        penaltyAmount = coolDownAmount - amontToUser;
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

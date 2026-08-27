### Title
Missing forfeit calculation in `mWOMSVBaseRewarder._calExpireForfeit` allows fully-unlocked mWomSV lockers to claim 100% of rewards instead of forfeiting the unearned share - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` unconditionally sets `rewardableAmount = _amount`, so `forfeitAmount` is always `0`, unlike its sibling contract `vlMGPBaseRewarder._calExpireForfeit`, which correctly scales the reward by `vlMGP.getRewardablePercentWAD(_account)`. Because `mWOMSV` also exposes `getRewardablePercentWAD` (intended to be used the same way), this omission lets any staker who starts/completes an unlock of their full mWomSV position still receive their entire accrued reward instead of forfeiting the pro-rated portion to remaining lockers.

### Finding Description
`mWOMSVBaseRewarder._sendReward` computes `forfeitAmount` via `_calExpireForfeit(_account, userRewards[_rewardToken][_account])` [1](#0-0) , but the internal implementation never queries `mWOMSV.getRewardablePercentWAD(_account)`:

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

By contrast, the near-identical `vlMGPBaseRewarder._calExpireForfeit` correctly does:
```solidity
uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
``` [3](#0-2) 

`mWomSV.getRewardablePercentWAD` exists and is designed to compute a decayed rewardable percentage for lockers that are in cool-down or fully unlocked, reducing the percent proportionally to time elapsed in the unlock window relative to the cool-down duration, so that partial/expired lockers forfeit unearned yield to remaining lockers [4](#0-3) . Since `mWOMSVBaseRewarder` never calls this getter, this forfeiture mechanism is entirely dead code for mWomSV.

Exploit path: an unprivileged attacker locks mWOM in `mWomSV` via `lock()`, which credits `MasterMagpie` staking balance [5](#0-4) . Rewards accrue in `mWOMSVBaseRewarder` over time. The attacker calls `startUnlock(fullAmount)` and then `unlock(slotIndex)` to fully exit [6](#0-5) . Since `mWOMSVBaseRewarder.balanceOf` reads live `MasterMagpie` staking info (which decreases only after `unlock` calls `withdrawMWomSVFor`) [7](#0-6) , and because `getReward`/`getRewards` are called through `MasterMagpie.multiclaim` (an unprivileged, public reward-claim function) with `onlyMasterMagpie` as the only gate [8](#0-7) , the attacker can claim the full accrued reward via `_sendReward`, with `forfeitAmount` always `0` regardless of how much time was spent in cool-down or how much of the lock was expired. No existing modifier (`onlyMasterMagpie`, `nonReentrant`, `updateReward`) checks or corrects this, since they only gate access/reentrancy, not the amount calculation.

### Impact Explanation
This is theft of unclaimed yield that should have been forfeited to other `mWomSV` lockers, matching the "theft or permanent freezing of unclaimed yield" Immunefi impact class. Every unlocking mWomSV staker over-claims 100% of rewards instead of the `getRewardablePercentWAD`-scaled share, permanently diluting/depriving remaining lockers of the forfeited portion that the reward-forfeiture design intends to redistribute to them via `_queueNewRewardsWithoutTransfer`.

### Likelihood Explanation
No privileged role is needed — only holding mWOM and normal user flow (`lock` → `startUnlock` → `unlock` → `MasterMagpie.multiclaim`). This is fully repeatable by any staker on every unlock cycle, requiring no special capital beyond ordinary mWOM holdings, and is guaranteed to trigger every time rewards have accrued.

### Recommendation
Fix `mWOMSVBaseRewarder._calExpireForfeit` to mirror `vlMGPBaseRewarder._calExpireForfeit` by querying `mWOMSV.getRewardablePercentWAD(_account)` and scaling `rewardableAmount = _amount * rewardablePercentWAD / 1e18` before computing `forfeitAmount`.

### Proof of Concept
Foundry fork test plan:
1. Deploy/fork the protocol with `mWomSV`, `mWOMSVBaseRewarder`, and `MasterMagpie` wired together.
2. Two users, Alice and Bob, each `lock()` equal amounts of mWOM.
3. Queue rewards into `mWOMSVBaseRewarder` via `queueNewRewards`, advance time so rewards accrue equally to both.
4. Alice calls `startUnlock(fullAmount)`, waits for `coolDownInSecs`, then calls `unlock(slotIndex)`.
5. Alice calls `MasterMagpie.multiclaim([mWomSV])`, capturing the reward token amount transferred to her via `RewardPaid` event.
6. Independently compute expected reward using `mWomSV.getRewardablePercentWAD(alice)` (which should reflect that Alice was in cool-down/fully unlocked for part of the window) multiplied by Alice's earned reward.
7. Assert `actualPaid > expectedProratedAmount`, specifically that `actualPaid == fullEarnedAmount` (i.e., `forfeitAmount == 0`) despite `getRewardablePercentWAD(alice) < 1e18`, proving the forfeiture logic never applies.
8. Assert Bob's future claim does not receive the forfeited share that should have been queued via `_queueNewRewardsWithoutTransfer`, confirming the loss is not merely accounting-neutral but a real transfer of value away from remaining lockers.

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L146-149)
```text
    function balanceOf(address _account) public override view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L233-261)
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

    function getRewards(address _account, address _receiver, address[] memory _rewardTokens)
        public
        onlyMasterMagpie
        updateRewards(_account, _rewardTokens)
        nonReentrant
    {
        uint256 length = _rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewardTokens[index];
            _sendReward(rewardToken, _account, _receiver);
        }
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

**File:** rewards/mWOMSVBaseRewarder.sol (L3622-376)
```text

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

**File:** wombat/mWomSV.sol (L247-303)
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

    // @notice unlock a finished slot
    // @param slotIndex the index of the slot to unlock
    function unlock(uint256 _slotIndex) external override whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        if (slot.endTime > block.timestamp)
            revert StillInCoolDown();

        if (slot.amountInCoolDown == 0)
            revert UnlockedAlready();

        address[] memory lps = new address[](1);
        address[][] memory mWomSVrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, mWomSVrewards, msg.sender);

        uint256 unlockedAmount = slot.amountInCoolDown;
        _unlock(unlockedAmount);

        slot.amountInCoolDown = 0;
        IERC20(mWOM).safeTransfer(msg.sender, unlockedAmount);

        emit Unlock(msg.sender, block.timestamp, unlockedAmount);
    }
```

**File:** wombat/mWomSV.sol (L370-378)
```text
    function _lock(
        address spender,
        address _for,
        uint256 _amount
    ) internal {
        mWOM.safeTransferFrom(spender, address(this), _amount);
        IMasterMagpie(masterMagpie).depositMWomSVFor(_amount, _for);
        totalAmount += _amount; // trigers update pool share, so happens after toal amount increase
    }
```

This confirms the vulnerability. The `_calExpireForfeit` function in `mWOMSVBaseRewarder` is dead code that hardcodes `rewardableAmount = _amount` instead of calling `mWOMSV.getRewardablePercentWAD(_account)` as the analogous `vlMGPBaseRewarder._calExpireForfeit` does with `vlMGP.getRewardablePercentWAD(_account)`.

### Title
Missing forfeit-percent check in mWOMSVBaseRewarder allows cooling-down mWomSV holders to earn full bonus rewards - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` never calls `mWomSV.getRewardablePercentWAD`, unlike its sibling `vlMGPBaseRewarder._calExpireForfeit` which correctly queries `vlMGP.getRewardablePercentWAD`. As a result, `forfeitAmount` is always computed as `_amount - _amount = 0`, so any user who has started unlocking (`startUnlock`) still accrues and claims full rewards for the entire cooldown period, exactly as if they were still fully locked.

### Finding Description
`mWOMSVBaseRewarder.balanceOf` reads the user's full staked balance from `MasterMagpie.stakingInfo`, which does not distinguish between locked and cooling-down mWomSV [1](#0-0) . Reward accrual (`_earned`/`rewardPerToken`) is entirely based on this full balance, and on claim, `_sendReward` calls `_calExpireForfeit` to determine how much of the accrued reward should be forfeited for users partially or fully in cooldown [2](#0-1) .

In the correct implementation (`vlMGPBaseRewarder`), `_calExpireForfeit` queries `vlMGP.getRewardablePercentWAD(_account)` to compute a decayed `rewardableAmount` based on how much of the user's balance is still fully locked versus in cooldown [3](#0-2) . In `mWOMSVBaseRewarder`, this logic is missing entirely — `rewardableAmount` is hardcoded to `_amount`, making `forfeitAmount` always zero: [4](#0-3) 

Meanwhile, `mWomSV.getRewardablePercentWAD` exists and correctly implements the decay formula (100% for locked, time-decayed for started cooldown) [5](#0-4) , but it is simply never invoked by the rewarder. `startUnlock` does not reduce the user's `balanceOf` in `MasterMagpie` (it only moves the amount into a cooldown slot, which is still counted in `getUserAmountInCoolDown` and thus in `balanceOf`) [6](#0-5) . Combined with the missing forfeit check, a user calling `startUnlock` for their full balance suffers no reward penalty at all and will keep accruing/claiming full rewards for the entire cooldown period via `getReward`/`getRewards`, which are only gated by `onlyMasterMagpie` and `nonReentrant`, with no forfeit-percent enforcement blocking this path [7](#0-6) .

### Impact Explanation
This is a theft-of-unclaimed-yield issue: reward pools are shared pro-rata via `rewardPerTokenStored`, funded either externally via `queueNewRewards`/`donateRewards` or internally via forfeited amounts routed back through `_queueNewRewardsWithoutTransfer`. Since the forfeit mechanism never fires, cooling-down (effectively unstaking) users retain their full pro-rata share of rewards instead of forfeiting a decaying portion back to the pool for other stakers, permanently diluting/stealing yield that fully-locked mWomSV holders should have received. This matches the "theft or permanent freezing of unclaimed yield" impact class.

### Likelihood Explanation
This requires no special privilege — any unprivileged holder of mWomSV (obtained by locking mWOM, which is permissionless) can call `startUnlock` for their full balance immediately after locking and continue to call `getReward`/`getRewards` through MasterMagpie for the entire cooldown duration, receiving full rewards. This is deterministic, repeatable by any staker, and requires no flash loans, front-running, or timing tricks — it is triggered by the normal user flow (`lock` → `startUnlock` → `getReward`).

### Recommendation
Fix `mWOMSVBaseRewarder._calExpireForfeit` to mirror `vlMGPBaseRewarder._calExpireForfeit`: query `mWOMSV.getRewardablePercentWAD(_account)` and compute `rewardableAmount = _amount * rewardablePercentWAD / 1e18` before determining `forfeitAmount`.

### Proof of Concept
Foundry test plan:
1. Deploy `mWomSV`, `MasterMagpie`, and `mWOMSVBaseRewarder`, register a reward token, and set `rewardManager` as a manager.
2. Two users (`userA`, `userB`) each lock an identical amount of mWOM via `mWomSV.lock`, staking receipts recorded in `MasterMagpie`.
3. `userB` immediately calls `mWomSV.startUnlock(fullAmount)` for their entire locked balance; `userA` remains fully locked.
4. Manager calls `mWOMSVBaseRewarder.queueNewRewards` multiple times across the cooldown period (`vm.warp` between calls) to simulate ongoing reward distribution.
5. After the cooldown period elapses (but before `userB` calls `unlock`), both users call `getReward` (or `getRewards`) via `MasterMagpie`.
6. Assert: `userA`'s claimed reward equals `userB`'s claimed reward (both receive full share), while independently computing `mWomSV.getRewardablePercentWAD(userB)` at each queue point shows it decaying below 100%, proving the forfeit mechanism computed by `_calExpireForfeit` never reduces `userB`'s payout despite `getRewardablePercentWAD` indicating it should.

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L146-149)
```text
    function balanceOf(address _account) public override view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L249-261)
```text
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

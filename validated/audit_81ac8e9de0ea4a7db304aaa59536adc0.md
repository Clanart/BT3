This confirms the finding. `mWOMSVBaseRewarder._calExpireForfeit` at [1](#0-0)  sets `rewardableAmount = _amount` unconditionally, unlike the analogous `vlMGPBaseRewarder._calExpireForfeit` at [2](#0-1)  which multiplies by `vlMGP.getRewardablePercentWAD(_account)`. The `mWomSV.getRewardablePercentWAD` function exists and is fully implemented at [3](#0-2)  but is never called by `mWOMSVBaseRewarder`, so it is dead code — `calExpireForfeit` and internal forfeiture always return 0 regardless of cooldown state.

### Title
mWOMSVBaseRewarder never forfeits reward for mWomSV holders in cooldown, permanently misallocating yield meant for fully-locked stakers - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` (and the public `calExpireForfeit`) hardcodes `rewardableAmount = _amount`, so `forfeitAmount` is always `0`, unlike its sibling `vlMGPBaseRewarder`, which correctly scales the claimable amount by `vlMGP.getRewardablePercentWAD(_account)`. Since `mWomSV.getRewardablePercentWAD` is implemented and meant to penalize cooldown/unlocking balances, any mWomSV holder in cooldown who calls `getReward` receives 100% of accrued rewards instead of the reduced, forfeiture-adjusted amount, permanently denying the redistribution of forfeited yield to still-locked stakers.

### Finding Description
`mWOMSVBaseRewarder.balanceOf` counts a user's full staked mWomSV position (via `MasterMagpie.stakingInfo`), which includes amounts placed in cooldown by `mWomSV.startUnlock` [4](#0-3) , exactly mirroring `VLMGP.balanceOf` = `getUserTotalLocked + getUserAmountInCoolDown` [5](#0-4) . Both reward contracts therefore accrue reward-per-token on the full (locked + cooling-down) balance.

To offset the fact that cooling-down balance no longer carries full governance/utility, `vlMGPBaseRewarder._sendReward` reduces the payout via `_calExpireForfeit`, which calls `vlMGP.getRewardablePercentWAD(_account)` to compute what fraction of rewards is still claimable, sending the forfeited remainder back into the pool via `_queueNewRewardsWithoutTransfer` (redistributed to all remaining stakers) [6](#0-5) .

`mWOMSVBaseRewarder._calExpireForfeit`, however, never references `mWOMSV.getRewardablePercentWAD(_account)` at all — it sets `rewardableAmount = _amount`, making the subsequent `forfeitAmount = _amount - rewardableAmount` always `0` [1](#0-0) . This is called both from the public `calExpireForfeit(account, rewardToken)` view [7](#0-6)  and from `_sendReward`, which is invoked on every `getReward` call [8](#0-7) .

No modifier, `nonReentrant` guard, or receipt-token accounting compensates for this — the check is simply missing/dead code, so `_queueNewRewardsWithoutTransfer` for mWomSV rewards is unreachable in practice (forfeitAmount is always 0).

### Impact Explanation
This falls under "theft or permanent freezing of unclaimed yield." Any mWomSV holder (attacker or otherwise) who has started an unlock/cooldown continues accruing full reward-per-token share and, upon calling `getReward`, receives 100% of it instead of the reduced amount intended by the protocol design (as implemented for the analogous vlMGP system). The portion that should have been forfeited and redistributed to stakers who remain fully locked is never redirected to them — it is permanently and systematically diverted to cooling-down accounts instead, at the expense of fully-locked stakers who would otherwise receive that redistributed yield.

### Likelihood Explanation
This requires no privileged role and no special timing/front-running — it is a deterministic, unconditional bug: `_calExpireForfeit` always returns 0 for every account regardless of cooldown status. Any existing mWomSV holder who calls `startUnlock` and later `getReward` (or `unlock`, which also triggers claim) automatically benefits from this every single time, at zero additional cost or complexity beyond normal protocol usage. It is fully repeatable and does not depend on flash loans, reentrancy, or precise front-running — the “front-run getReward with a cooldown” framing in the question is unnecessary because the missing check applies universally to every claim, not just ones immediately following a cooldown start.

### Recommendation
In `rewards/mWOMSVBaseRewarder.sol`, fix `_calExpireForfeit` to mirror `vlMGPBaseRewarder`:
```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardablePercentWAD = mWOMSV.getRewardablePercentWAD(_account);
    uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
    if (rewardableAmount > _amount) revert InvalidRewardableAmount();
    uint256 forfeitAmount = _amount - rewardableAmount;
    if (forfeitAmount < (_amount / 1000)) {
        forfeitAmount = 0;
        rewardableAmount = _amount;
    }
    return forfeitAmount;
}
```
This requires `ILocker`/`mWomSV` to expose `getRewardablePercentWAD`, which is already implemented in `wombat/mWomSV.sol`.

### Proof of Concept
Hardhat test plan:
1. Deploy `mWomSV`, `MasterMagpie`, and `mWOMSVBaseRewarder` in a test harness (or use existing fixture).
2. Have two accounts, Alice and Bob, each `lock` an equal amount of mWom, then queue reward tokens via `queueNewRewards` so both accrue equal `rewardPerTokenStored`.
3. Alice calls `startUnlock` for her full balance, moving it into cooldown (`endTime = now + coolDownInSecs`).
4. Immediately (or partway through cooldown) call `mWOMSVBaseRewarder.calExpireForfeit(alice, rewardToken)` and separately compute the expected forfeit manually as `earned(alice, rewardToken) * (1e18 - mWomSV.getRewardablePercentWAD(alice)) / 1e18`.
5. Assert: expected (manually computed) forfeit is `> 0` (since Alice is mid-cooldown, `getRewardablePercentWAD` < 1e18), but `calExpireForfeit` returns `0`.
6. Have Alice call `getReward` and assert she receives the full `earned` amount (no reduction), while Bob's later claim receives no benefit from any redistributed forfeit (`ForfeitRewardAdded` event never emitted, `queuedRewards`/`rewardPerTokenStored` never incremented via `_queueNewRewardsWithoutTransfer`).
7. Compare against an equivalent `vlMGPBaseRewarder` test using `VLMGP.startUnlock`, showing that in that contract `calExpireForfeit`/`_sendReward` do produce a nonzero forfeit and emit `ForfeitRewardAdded`, confirming the mWomSV path is uniquely broken.

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L188-190)
```text
    function calExpireForfeit(address _account, address _rewardToken) public view returns(uint256) {
        return _calExpireForfeit(_account, earned(_account, _rewardToken));
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

**File:** VLMGP.sol (L113-115)
```text
    function balanceOf(address _user) public override view returns (uint256) {
        return getUserTotalLocked(_user) + getUserAmountInCoolDown(_user);
    }
```

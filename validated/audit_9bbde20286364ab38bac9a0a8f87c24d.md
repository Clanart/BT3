Confirmed: `mWOMSVBaseRewarder._calExpireForfeit` sets `rewardableAmount = _amount` unconditionally and never calls `getRewardablePercentWAD`, unlike `vlMGPBaseRewarder._calExpireForfeit` which multiplies by `vlMGP.getRewardablePercentWAD(_account)` [1](#0-0)  versus [2](#0-1) . `mWomSV` does implement `getRewardablePercentWAD`, which reduces the percent for users still in cooldown [3](#0-2) , but this function is never invoked by `mWOMSVBaseRewarder`, so `getReward`/`getRewards` always send 100% of `userRewards` via `_sendReward` regardless of cooldown state [4](#0-3) .

However, evaluating this against the "real economic loss" bar: the forfeiture mechanism here doesn't cause any external funds to be stolen from other users' principal — the forfeited amount (when present) would just be re-queued as rewards for the same pool via `_queueNewRewardsWithoutTransfer`, i.e., a slashed portion that would otherwise go back into the reward pool for other stakers [5](#0-4) . When the reduction is skipped, that user simply keeps 100% of their own already-accrued rewards instead of forfeiting a slashed portion back to the pool. This is a real yield-misallocation bug (other lockers lose the redistributed forfeit share they'd otherwise receive), matching "theft of intended-forfeited share of yield" for other lockers, which fits the "theft or permanent freezing of unclaimed yield" impact class.

### Title
mWOMSVBaseRewarder never applies cooldown-based reward slashing, allowing cooldown users to claim 100% rewards instead of forfeiting a share to other lockers - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` unconditionally sets `rewardableAmount = _amount`, never consulting `mWOMSV.getRewardablePercentWAD(_account)` as the analogous `vlMGPBaseRewarder._calExpireForfeit` does for `vlMGP`. As a result `forfeitAmount` is always `_amount - _amount = 0`, so any mWomSV holder mid-cooldown who calls `getReward`/`getRewards` receives their full accrued reward with zero slashing, even though `mWomSV.getRewardablePercentWAD` explicitly computes a reduced percentage for cooling-down/unlocked balances.

### Finding Description
`_calExpireForfeit(address _account, uint256 _amount)` in `mWOMSVBaseRewarder` is:
```
uint256 rewardableAmount = _amount;
if (rewardableAmount > _amount) revert InvalidRewardableAmount();
uint256 forfeitAmount = _amount - rewardableAmount; // always 0
```
Compare to `vlMGPBaseRewarder._calExpireForfeit`, which computes `rewardableAmount = _amount * vlMGP.getRewardablePercentWAD(_account) / 1e18` before subtracting. `mWOMSVBaseRewarder` holds a reference to `ILocker mWOMSV`, and `ILocker`/`mWomSV.sol` exposes `getRewardablePercentWAD(_user)` which reduces the rewardable percent proportionally to the amount currently in cooldown/unlocking (`wombat/mWomSV.sol` lines 181-206), but this getter is never called anywhere in `mWOMSVBaseRewarder.sol`. Both `getReward` and `getRewards` route through `_sendReward`, which calls `_calExpireForfeit` and always gets `forfeitAmount == 0`, so the entire `userRewards` balance is transferred to the receiver with nothing routed to `_queueNewRewardsWithoutTransfer`.

### Impact Explanation
This breaks the intended forfeiture/slashing invariant for `mWomSV` cooldown lockers: forfeited yield that should be redistributed to other lockers (via `_queueNewRewardsWithoutTransfer`) never accumulates, so remaining/fully-locked stakers permanently lose the yield share they were entitled to receive from slashed cooldown users. This is a protocol-yield-misallocation/theft-of-unclaimed-yield issue affecting all mWomSV reward pools using this rewarder, but it does not touch user principal or allow draining of unrelated funds — the loss is confined to the intended-forfeit portion of reward distribution.

### Likelihood Explanation
No special capital or privilege is required: any mWomSV holder can call `startUnlock`, wait, and then call `getReward`/`getRewards` (via MasterMagpie) at any time — including immediately after unlocking starts, well before cooldown ends — and always receive 100% of `earned()` rather than the reduced amount `getRewardablePercentWAD` would imply. The bug is deterministic and repeatable on every reward claim by every cooldown user, with no need for flash loans or reentrancy.

### Recommendation
Update `mWOMSVBaseRewarder._calExpireForfeit` to mirror `vlMGPBaseRewarder._calExpireForfeit`: compute `uint256 rewardablePercentWAD = mWOMSV.getRewardablePercentWAD(_account);` and `uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;` before deriving `forfeitAmount`, ensuring cooldown/unlocked mWomSV holders are slashed proportionally like `vlMGP` holders.

### Proof of Concept
Foundry test plan:
1. Deploy `mWomSV`, `MasterMagpie`, and `mWOMSVBaseRewarder`; register a reward token and set the rewarder as reward pool for `mWomSV` staking token in `MasterMagpie`.
2. User A locks `1000 mWOM` via `mWomSV.lock`.
3. User A calls `startUnlock(500)` to put half their balance into cooldown (`getRewardablePercentWAD(A)` should now be < 1e18, e.g. ~50%).
4. Manager calls `mWOMSVBaseRewarder.queueNewRewards(rewardAmount, rewardToken)` to inject rewards while A is fully in the pool's `balanceOf` (staked amount includes cooldown balance per `mWOMSVBaseRewarder.balanceOf`).
5. Before cooldown `endTime`, call `MasterMagpie` path leading to `mWOMSVBaseRewarder.getReward(A, A)`.
6. Assert: `IERC20(rewardToken).balanceOf(A)` equals `earned(A, rewardToken)` in full (100%), and `rewards[rewardToken].queuedRewards`/`rewardPerTokenStored` show zero re-queued forfeit — i.e., no slashing occurred despite `mWomSV.getRewardablePercentWAD(A) < 1e18`.
7. Contrast with an equivalent `vlMGPBaseRewarder` test showing `forfeitAmount > 0` and re-queuing occurs under the same cooldown conditions, confirming the divergence is a bug specific to `mWOMSVBaseRewarder`.

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L330-346)
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
                (_amountReward * 10**mWOMSVDecimal) / totalStaked();
        }
        emit ForfeitRewardAdded(_amountReward, _rewardToken);
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

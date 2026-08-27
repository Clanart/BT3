### Title
Missing forfeiture enforcement in mWOMSVBaseRewarder allows permanent 0% penalty on rewards despite cooldown state - ([File: rewards/mWOMSVBaseRewarder.sol])

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` never queries `mWomSV.getRewardablePercentWAD(_account)`, unlike the parallel `vlMGPBaseRewarder._calExpireForfeit` which correctly scales `rewardableAmount` by `vlMGP.getRewardablePercentWAD(_account)`. As a result `rewardableAmount` is hardcoded to equal `_amount`, so `forfeitAmount` is always `0` regardless of how much of a user's `mWomSV` balance is sitting in cooldown slots.

### Finding Description
`mWomSV.getRewardablePercentWAD` (`wombat/mWomSV.sol:181-206`) is explicitly designed to compute a penalty percentage based on how much of a user's balance is fully locked vs. how much is in active cooldown (`startUnlock`), mirroring the exact same mechanism used by `vlMGP.getRewardablePercentWAD` and consumed correctly in `vlMGPBaseRewarder._calExpireForfeit` (`rewards/vlMGPBaseRewarder.sol:386-400`):
```
uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
```
The equivalent function in `mWOMSVBaseRewarder` (`rewards/mWOMSVBaseRewarder.sol:385-398`) never calls `mWOMSV.getRewardablePercentWAD`:
```
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardableAmount = _amount;
    if (rewardableAmount > _amount)
        revert InvalidRewardableAmount();
    uint256 forfeitAmount = _amount - rewardableAmount;
    ...
}
```
Since `rewardableAmount` is always set equal to `_amount`, `forfeitAmount` is always `0` and the `if (rewardableAmount > _amount)` sanity check is dead code that can never trigger.

Critically, `mWomSV.balanceOf` (`wombat/mWomSV.sol:101-103`) and `IMasterMagpie.stakingInfo` used by `mWOMSVBaseRewarder.balanceOf` (`rewards/mWOMSVBaseRewarder.sol:146-149`) do not reduce a user's staked/earning weight when they call `startUnlock` — cooldown tokens continue to count at full weight (`getUserTotalLocked + getUserAmountInCoolDown`), so a cooling-down staker earns rewards identically to a fully-locked staker. The `_calExpireForfeit` forfeiture step was the only intended mechanism to claw back a share of rewards from users partially/fully in cooldown and redistribute it to genuinely committed lockers via `_queueNewRewardsWithoutTransfer` (`rewards/mWOMSVBaseRewarder.sol:330-346`). Because forfeiture never fires, any `mWomSV` holder can call `mWomSV.startUnlock` (repeatedly, across `maxSlot` slots) to place their entire balance in cooldown while retaining full earning weight and full reward payout with zero penalty, contrary to design and unlike the `vlMGP` counterpart.

No existing modifier (`onlyMasterMagpie`, `updateReward`, `nonReentrant`) prevents this — the flaw is purely in the forfeiture math, and it is reachable by any unprivileged `mWomSV` holder through the normal `MasterMagpie.claim` → `mWOMSVBaseRewarder.getReward` → `_sendReward` → `_calExpireForfeit` path.

### Impact Explanation
Users who should be penalized for having their `mWomSV` in cooldown (a state that is one step away from fully liquid/unlocked) instead retain 100% of their rewards. The forfeited share, which was meant to be recycled into `rewardPerTokenStored` for the benefit of remaining, genuinely-committed lockers via `_queueNewRewardsWithoutTransfer`, never materializes. This is a permanent transfer of value away from stakers who don't churn their lock state toward stakers who deliberately keep some/all of their balance perpetually cycling through cooldown slots, matching the Immunefi impact class of theft/permanent loss of unclaimed yield for the intended beneficiaries of the forfeiture mechanism. It does not, however, create a balance-sheet insolvency in the sense of `mWOMSVBaseRewarder` paying out more reward tokens than it has received via `queueNewRewards`/`donateRewards`, since the reward-per-share accounting remains internally self-consistent — the loss is of the *forfeit-and-redistribute* upside, not of already-deposited principal.

### Likelihood Explanation
Trivial and fully permissionless: any `mWomSV` holder can call `startUnlock` for any/all of their locked balance across up to `maxSlot` slots, and repeat/cancel/restart cooldowns indefinitely (`cancelUnlock` lets them reset a slot without ever completing withdrawal) with no capital cost beyond gas, guaranteeing zero forfeiture on every subsequent claim through `getReward`/`getRewards`. This requires no special timing, flash loans, or governance/admin rights, and is repeatable by every user of the pool.

### Recommendation
Update `mWOMSVBaseRewarder._calExpireForfeit` to mirror `vlMGPBaseRewarder._calExpireForfeit`: fetch `mWOMSV.getRewardablePercentWAD(_account)` and scale `rewardableAmount = _amount * rewardablePercentWAD / 1e18` before computing `forfeitAmount`, and ensure `getRewardablePercentWAD` is exposed on the `ILocker` interface used by `mWOMSVBaseRewarder`.

### Proof of Concept
Foundry test plan:
1. Deploy `mWomSV`, `MasterMagpie`, and `mWOMSVBaseRewarder`; fund two users A (attacker) and B (honest committed locker) with equal `mWOM`, both `lock()` the same amount.
2. Have A call `mWomSV.startUnlock` for their full balance (across available slots) immediately, and periodically call `cancelUnlock`/`startUnlock` again to keep perpetually in cooldown without ever finishing `unlock`.
3. Manager calls `queueNewRewards` to inject reward tokens over multiple epochs.
4. Both A and B call `MasterMagpie.claim` → `mWOMSVBaseRewarder.getReward`.
5. Assert `mWOMSVBaseRewarder.calExpireForfeit(A, rewardToken)` returns `0` at every step (bug reproduction) while an equivalent scenario run against `vlMGPBaseRewarder`/`vlMGP` (same cooldown-state ratio) returns non-zero, proving the missing `getRewardablePercentWAD` gating causes permanent 0-forfeiture in `mWOMSVBaseRewarder` regardless of lock state, and that B's `rewardPerTokenStored` never receives the recycled forfeiture inflow it would have received had the mechanism worked as in `vlMGPBaseRewarder`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** wombat/mWomSV.sol (L97-117)
```text
    function totalSupply() public override view returns (uint256) {
        return totalAmount;
    }

    function balanceOf(address _user) public override view returns (uint256) {
        return getUserTotalLocked(_user) + getUserAmountInCoolDown(_user);
    }

    // total mWom locked, excluding the ones in cool down
    function totalLocked() override public view returns (uint256) {
        return this.totalSupply() - this.totalAmountInCoolDown();
    }

    /// @notice Get the total mWom a user locked, not counting the ones in cool down
    /// @param _user the user
    /// @return _lockAmount the total mWom a user locked, not counting the ones in cool down
    function getUserTotalLocked(address _user) override public view returns (uint256 _lockAmount) {
        // needs fixing
        (uint256 _amountInMasterMagpie, ) = IMasterMagpie(masterMagpie).stakingInfo(address(this), _user);
        _lockAmount = _amountInMasterMagpie - getUserAmountInCoolDown(_user);
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

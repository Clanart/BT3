### Title
Broken forfeit calculation in `mWOMSVBaseRewarder._calExpireForfeit` allows unlocking mWomSV holders to keep earning full rewards - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` never reads `mWOMSV.getRewardablePercentWAD(_account)`, so `rewardableAmount` is always set equal to `_amount`, making `forfeitAmount` unconditionally `0`. As a result, any staker who moves part or all of their mWomSV into a cooldown/unlock slot via `mWomSV.startUnlock` continues to receive 100% of their accrued bonus rewards through `getReward`/`getRewards`, exactly like a fully-locked staker, and the forfeited share that should be redistributed to fully-locked holders is never generated.

### Finding Description
`rewards/mWOMSVBaseRewarder.sol`'s `_calExpireForfeit` is: [1](#0-0) 
```
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardableAmount = _amount;
    if (rewardableAmount > _amount)
        revert InvalidRewardableAmount();

    uint256 forfeitAmount = _amount - rewardableAmount;
    ...
    return forfeitAmount;
}
```
`rewardableAmount` is initialized directly to `_amount`, so `forfeitAmount = _amount - rewardableAmount` is always `0`. The sibling contract `rewards/vlMGPBaseRewarder.sol` implements the equivalent function correctly by querying the locker's rewardable percent: [2](#0-1) 
```
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
    uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
    ...
```
This confirms the intended design: reward eligibility should scale down with `getRewardablePercentWAD`, which itself is implemented in `mWomSV.sol` and correctly discounts cooldown time remaining: [3](#0-2) 

`_sendReward` calls `_calExpireForfeit(_account, userRewards[...])` and since the forfeit is always `0`, the full `userRewards` amount is transferred with no discount: [4](#0-3) 

The attacker path is fully reachable by an unprivileged staker: they hold mWomSV recorded in MasterMagpie (via normal locking), call `mWomSV.startUnlock(amount)` (public, `nonReentrant`, `whenNotPaused`) to place their balance in a cooldown slot, and their `balanceOf` in `mWOMSVBaseRewarder` (`IMasterMagpie(masterMagpie).stakingInfo(...)`) is unaffected because `mWomSV.balanceOf` sums locked + in-cooldown amounts, so total staked weight in MasterMagpie does not decrease: [5](#0-4) [6](#0-5) 

Rewards continue to accrue through `queueNewRewards` (called by `rewardManager`) and `getReward`/`getRewards` (callable via MasterMagpie by the staker), and are paid out in full with no forfeit, since `_calExpireForfeit` never applies any discount.

### Impact Explanation
This is theft of unclaimed yield belonging to fully-locked users. The forfeit mechanism is designed so that unlocking/cooling-down stakers give up a pro-rata share of their bonus rewards, which is recycled back into the reward pool for remaining fully-locked stakers via `_queueNewRewardsWithoutTransfer`. Because `forfeitAmount` is always `0`, that redistribution never happens, and every unlocking staker captures 100% of rewards they were not economically entitled to, permanently diminishing the yield that fully-locked holders should have received.

### Likelihood Explanation
This requires no special capital or privilege — any staker who already holds mWomSV staked in MasterMagpie can call `startUnlock` at any time and continue to call `getReward`/`getRewards` normally. It is deterministic and repeatable on every reward distribution cycle for as long as the bug exists, since the broken logic is unconditional and independent of timing, amount, or cooldown state.

### Recommendation
Fix `_calExpireForfeit` in `rewards/mWOMSVBaseRewarder.sol` to mirror `vlMGPBaseRewarder`'s implementation by querying `mWOMSV.getRewardablePercentWAD(_account)` and scaling `rewardableAmount` accordingly:
```solidity
uint256 rewardablePercentWAD = mWOMSV.getRewardablePercentWAD(_account);
uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
```

### Proof of Concept
Foundry test plan:
1. Deploy `mWomSV`, `MasterMagpie`, and `mWOMSVBaseRewarder`, register a reward token, and set the rewarder as manager.
2. User A locks `100e18` mWomSV and stakes into MasterMagpie; User B locks `100e18` mWomSV and stakes into MasterMagpie.
3. User A calls `mWomSV.startUnlock(100e18)` to fully enter cooldown (their `getRewardablePercentWAD` should now be well below `1e18`, decreasing over the cooldown period per `mWomSV.getRewardablePercentWAD`). User B remains fully locked (`getRewardablePercentWAD == 1e18`).
4. `rewardManager` calls `queueNewRewards(1000e18, rewardToken)` on the rewarder.
5. Advance time partway into the cooldown window (before `endTime`).
6. Both users call `getReward` via MasterMagpie.
7. Assert: User A receives the same proportional share of rewards as User B (`toSend` amounts equal, both computed from `userRewards` with `forfeitAmount == 0`), and `calExpireForfeit(userA, rewardToken)` returns `0` despite `mWOMSV.getRewardablePercentWAD(userA) < 1e18`, proving no forfeit is ever applied and violating the intended conservation between locked and unlocking rewardable shares.

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L146-149)
```text
    function balanceOf(address _account) public override view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account);
        return staked;
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

**File:** rewards/vlMGPBaseRewarder.sol (L386-392)
```text
    function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
        uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
        uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
        if (rewardableAmount > _amount)
            revert InvalidRewardableAmount();

        uint256 forfeitAmount = _amount - rewardableAmount;
```

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

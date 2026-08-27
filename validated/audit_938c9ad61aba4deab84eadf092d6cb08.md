This confirms a real, exploitable discrepancy.

### Title
mWOMSVBaseRewarder._calExpireForfeit ignores getRewardablePercentWAD, allowing 100% claim of forfeitable bonus rewards - ([File: rewards/mWOMSVBaseRewarder.sol])

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` sets `rewardableAmount = _amount` unconditionally, never consulting `mWOMSV.getRewardablePercentWAD(_account)`, so `forfeitAmount` is always `0`. In contrast, the analogous `vlMGPBaseRewarder._calExpireForfeit` multiplies by `vlMGP.getRewardablePercentWAD(_account)` to compute the forfeitable share for accounts with expired/mid-cooldown positions. Any mWomSV holder with tokens sitting in cooldown (partially or fully) therefore receives 100% of their accrued bonus rewards via `getReward`/`getRewards`, instead of the reduced share the protocol design (mirrored in `mWomSV.getRewardablePercentWAD`) intends.

### Finding Description
`mWomSV.getRewardablePercentWAD` (in `wombat/mWomSV.sol`, lines 181–206) explicitly computes a rewardable percentage that penalizes tokens in cooldown: fully-locked tokens count at 100%, but tokens in an active cooldown slot count at a reduced pro-rated percentage, and only fully-unlocked (expired) cooldown slots that haven't been withdrawn get a time-weighted partial credit. This function exists specifically to support forfeiture logic like `vlMGPBaseRewarder._calExpireForfeit` (lines 386–400), which does:
```
uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
...
uint256 forfeitAmount = _amount - rewardableAmount;
```
`mWOMSVBaseRewarder._calExpireForfeit` (lines 385–398) instead does:
```
uint256 rewardableAmount = _amount;
if (rewardableAmount > _amount) revert InvalidRewardableAmount();
uint256 forfeitAmount = _amount - rewardableAmount;
```
Since `rewardableAmount` is hardcoded to `_amount`, `forfeitAmount` is always `0` (the `_amount / 1000` dust-ignoring branch is unreachable/no-op because forfeitAmount is already 0). This function is called from `_sendReward` (line 363), which is invoked by the public `getReward`/`getRewards` entry points — both callable by `masterMagpie` on behalf of any mWomSV holder, with no additional gating on cooldown state.

Exploit flow:
1. Attacker holds mWomSV and calls `startUnlock(largeAmount)` on `mWomSV.sol`, moving most of their balance into cooldown (`getRewardablePercentWAD` would then return a value well below `1e18` for a correctly-implemented forfeiture).
2. Bonus rewards accrue to the pool via `queueNewRewards` (`rewards/mWOMSVBaseRewarder.sol` line 278), increasing `rewardPerTokenStored`.
3. Attacker (or MasterMagpie on their behalf) calls `getReward(account, receiver)`, which calls `_sendReward` → `_calExpireForfeit`, which returns `forfeitAmount = 0` regardless of the account's cooldown state.
4. Attacker receives 100% of `userRewards`, instead of only the rewardable share; nothing is redistributed via `_queueNewRewardsWithoutTransfer` to remaining lockers.

Existing checks (`onlyMasterMagpie`, `updateReward`, `nonReentrant` on `getRewards`) do not address this because they gate *who* can call the function, not *how much* is forfeited — the forfeiture computation itself is broken.

### Impact Explanation
This is theft/misappropriation of unclaimed yield: the redistribution mechanism (forfeiture pool feeding back into `rewardPerTokenStored` for remaining lockers) never triggers for mWomSV rewarders, so accounts with any amount in cooldown always capture 100% of accrued bonus rewards. Long-term/fully-locked stakers who should receive the redistributed forfeiture from cooling-down/exiting accounts receive nothing extra, permanently losing that yield to unlocking accounts. This matches "theft or permanent freezing of unclaimed yield" — funds intended for one class of users are diverted to another with no privileged action required.

### Likelihood Explanation
High likelihood/trivial exploitability: no special capital or privilege is needed. Any unprivileged mWomSV holder who calls `startUnlock` (a normal, unprivileged public function) and later calls `getReward`/`getRewards` (through MasterMagpie, standard user flow) automatically benefits every time, with no way for the protocol to intervene short of a code fix. The behavior is deterministic and repeatable on every reward claim.

### Recommendation
Update `mWOMSVBaseRewarder._calExpireForfeit` to mirror `vlMGPBaseRewarder._calExpireForfeit`: query `mWOMSV.getRewardablePercentWAD(_account)` and compute `rewardableAmount = _amount * rewardablePercentWAD / 1e18` before deriving `forfeitAmount`, so cooldown/expired positions correctly forfeit the appropriate portion of bonus rewards back into the pool via `_queueNewRewardsWithoutTransfer`.

### Proof of Concept
Foundry test outline:
1. Deploy `mWomSV`, `MasterMagpie`, and `mWOMSVBaseRewarder`; register a reward token.
2. User A locks a large amount of mWOM via `mWomSV.lock`, staked in MasterMagpie.
3. User A calls `mWomSV.startUnlock(largeAmount)` to place most of the balance into cooldown; assert `mWomSV.getRewardablePercentWAD(userA) < 1e18` (confirms cooldown reduces rewardable share per protocol design).
4. Reward manager calls `mWOMSVBaseRewarder.queueNewRewards(rewardAmount, token)` to accrue rewards.
5. Call `mWOMSVBaseRewarder.calExpireForfeit(userA, token)` and assert it returns `0` (bug) versus computing the expected forfeit manually as `earned * (1e18 - getRewardablePercentWAD(userA)) / 1e18` (the correct, nonzero value using vlMGP's formula).
6. Call `getReward(userA, userA)` via MasterMagpie and assert User A receives the *full* `earned` amount (`toSend == userRewards` pre-claim) with zero routed to `_queueNewRewardsWithoutTransfer`, i.e., `ForfeitRewardAdded` event never emitted despite active cooldown — demonstrating the delta that should have been redistributed to remaining lockers was instead paid to the cooling-down attacker. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

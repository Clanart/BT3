### Title
Missing forfeiture multiplier in `mWOMSVBaseRewarder._calExpireForfeit` allows unlocking users to claim 100% of bonus rewards, permanently defeating yield redistribution to remaining lockers - (File: `rewards/mWOMSVBaseRewarder.sol`)

### Summary
`vlMGPBaseRewarder._calExpireForfeit` computes `rewardableAmount = _amount * vlMGP.getRewardablePercentWAD(_account) / 1e18` so that users who are unlocking/have unlocked forfeit a pro-rata share of bonus rewards back into the pool for remaining lockers. The analogous `mWOMSVBaseRewarder._calExpireForfeit` never calls any rewardable-percent function and instead hardcodes `rewardableAmount = _amount`, so `forfeitAmount` is always `0` regardless of the caller's lock/cooldown/unlock state.

### Finding Description
In `rewards/vlMGPBaseRewarder.sol`, `_calExpireForfeit` correctly discounts rewards by the fraction of tokens actually still locked: [1](#0-0) 
This uses `IVLMGP.getRewardablePercentWAD`, which itself is computed in `VLMGP.sol` from the user's locked vs. cooling-down/unlocked balances: [2](#0-1) 

The mirrored function in `rewards/mWOMSVBaseRewarder.sol` is structurally identical but omits the percent lookup entirely: [3](#0-2) 
Here `rewardableAmount` is initialized to `_amount` and never reduced, so `forfeitAmount = _amount - rewardableAmount` is always `0`. Consistently, `mWomSV.sol` (the mWOM staking vault) exposes no `getRewardablePercentWAD`-style function at all — confirmed by inspecting its public getters (`totalSupply`, `balanceOf`, `getUserTotalLocked`, `getUserAmountInCoolDown`, etc.), none of which are wired into the rewarder's forfeit calculation.

`_sendReward` in `mWOMSVBaseRewarder.sol` calls `_calExpireForfeit` for every claim: [4](#0-3) 
Since `forfeitAmount` is always `0`, `_queueNewRewardsWithoutTransfer` (the path that redistributes forfeited rewards to remaining stakers) is never triggered from this rewarder, and `toSend` always equals the user's full accrued `userRewards`.

Exploit flow: an attacker (1) acquires mWOM and calls `mWomSV.lock`, accruing bonus rewards in `mWOMSVBaseRewarder` over time while their tokens are fully locked (weighted 100% same as any staker); (2) calls `mWomSV.startUnlock` to begin cooldown, which itself triggers `multiclaimFor` — at this point the vlMGP analog would already forfeit a portion via `getRewardablePercentWAD` reflecting the mid-cooldown state, but mWOMSVBaseRewarder does not; (3) after cooldown, calls `mWomSV.unlock`, which again triggers `multiclaimFor`; (4) or directly calls `MasterMagpie.multiclaimFor` at any point. In every case `_calExpireForfeit` returns `0`, so the attacker receives 100% of `userRewards` with no forfeiture, unlike the equivalent vlMGP flow which enforces a penalty proportional to time spent unlocking/unlocked.

No existing modifier (`onlyMasterMagpie`, `updateReward`, `nonReentrant`) prevents this — they gate access and update accounting correctly, but the forfeiture computation itself is simply missing the percentage multiplier that the vlMGP counterpart has.

### Impact Explanation
This causes permanent loss of the forfeiture-based yield redistribution mechanism for mWOMSVBaseRewarder: reward that should be clawed back from users who unlock/are unlocking and redistributed to `rewardPerTokenStored` for remaining lockers (via `_queueNewRewardsWithoutTransfer`) is instead paid out in full to the exiting user. This matches the "theft or permanent freezing of unclaimed yield" impact class — remaining long-term lockers permanently lose their expected redistributed share, and unlocking users capture rewards they should not be entitled to under the protocol's own forfeiture design.

### Likelihood Explanation
This requires no special privileges — any holder of mWOM can lock via `mWomSV.lock`, wait for rewards to accrue, and claim via `mWomSV.startUnlock`/`unlock` or `MasterMagpie.multiclaimFor`. It is deterministic (not probabilistic), fully repeatable by any user on every claim, and requires no flash loans, front-running, or capital beyond the mWOM being locked. Because the bug is a hardcoded logic omission (not a timing race), it triggers on every single claim through this rewarder.

### Recommendation
Add an equivalent rewardable-percent computation to `mWomSV.sol` (mirroring `VLMGP.getRewardablePercentWAD`, based on `getUserTotalLocked` vs. `getUserAmountInCoolDown`/unlocked amounts), and update `mWOMSVBaseRewarder._calExpireForfeit` to call it: `uint256 rewardablePercentWAD = ILocker(stakingToken).getRewardablePercentWAD(_account); uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;` matching the vlMGP implementation exactly.

### Proof of Concept
Foundry fork test plan:
1. Deploy/fork the protocol with `mWomSV`, `mWOMSVBaseRewarder`, and `MasterMagpie` wired as in production.
2. Have attacker acquire mWOM and call `mWomSV.lock(amount)`.
3. Have `rewardManager` queue bonus rewards into `mWOMSVBaseRewarder` via `queueNewRewards`, advance time so `rewardPerTokenStored` accrues for the attacker.
4. Call `mWomSV.startUnlock(amount)` then, after `coolDownInSecs`, call `mWomSV.unlock(slotIndex)` (each internally calls `MasterMagpie.multiclaimFor`).
5. Assert: attacker's `IERC20(rewardToken).balanceOf(attacker)` increases by the *full* `earned(attacker, rewardToken)` amount computed before unlock — i.e., `toSend == userRewards` and `forfeitAmount == 0` for every claim, regardless of cooldown/unlock state.
6. Contrast: repeat the same sequence against `vlMGPBaseRewarder`/`VLMGP` with an unlocking vlMGP holder and assert `forfeitAmount > 0` there, proving the discrepancy.
7. Assert `rewards[rewardToken].rewardPerTokenStored` in `mWOMSVBaseRewarder` for other remaining stakers never increases from `_queueNewRewardsWithoutTransfer` despite repeated unlocks by other users — confirming forfeited yield is never redistributed.

### Citations

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

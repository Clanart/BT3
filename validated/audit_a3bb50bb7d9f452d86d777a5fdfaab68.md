### Title
Forfeit-on-early-exit mechanism is entirely dead code in `mWOMSVBaseRewarder`, allowing 100% yield claim with zero penalty - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`_calExpireForfeit()` in `rewards/mWOMSVBaseRewarder.sol` never actually invokes `mWOMSV.getRewardablePercentWAD(_account)` (unlike its sibling `vlMGPBaseRewarder._calExpireForfeit()`, which does). It sets `rewardableAmount = _amount` unconditionally, so `forfeitAmount` is always `0` regardless of whether the account is mid-cooldown or holds fully locked tokens. As a result, any account calling `getRewards`/`getReward` through `MasterMagpie` always receives 100% of pending rewards with no forfeiture, even when it should be penalized for having started an unlock.

### Finding Description
Compare the two forfeiture implementations: [1](#0-0) 

versus [2](#0-1) 

In `vlMGPBaseRewarder`, `rewardableAmount` is derived from `vlMGP.getRewardablePercentWAD(_account)`, which encodes the actual lock/cooldown state and time served. In `mWOMSVBaseRewarder`, the equivalent line is missing entirely — `rewardableAmount` is hardcoded to `_amount`, making `forfeitAmount = _amount - rewardableAmount` always `0`, independent of `mWOMSV.getRewardablePercentWAD(_account)` [3](#0-2)  or of the caller's cooldown/lock state.

This function is reached from `_sendReward`, which is called from both `getReward` and `getRewards` — the latter being externally reachable by an unprivileged EOA through `MasterMagpie.multiclaimFor`/`multiclaimSpec`, with `onlyMasterMagpie` as the only access control: [4](#0-3) [5](#0-4) 

Because `forfeitAmount` is unconditionally `0` (which is always `< _amount/1000`), the dust-suppression branch at line 392 is trivially satisfied on every single call — there is no need to "land just below the threshold"; the code is simply non-functional and always waives forfeiture. Note that `balanceOf(_account)` — sourced directly from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account).staked` — is untouched by this bug and remains reconciled at all times, since `_calExpireForfeit`/`_sendReward` never write to staking balances; the invariant framed in the question about `balanceOf` desync does not actually apply here. The real defect is purely in reward accounting: the loyalty/forfeiture penalty designed to route a portion of early-exit rewards back into the pool for remaining stakers (`_queueNewRewardsWithoutTransfer`) never fires.

### Impact Explanation
Any unprivileged staker who starts a cooldown/unlock in `mWomSV` (`startUnlock`) and then calls `getRewards`/`getReward` (directly or via `MasterMagpie.multiclaimFor`/`multiclaimSpec`) receives their full pending reward with zero forfeiture, even though the intended design (mirrored in `vlMGPBaseRewarder`) is to forfeit a pro-rated share of rewards for time not fully "served" in lock. This is a systemic, unconditional loss of the forfeiture revenue that should be redistributed as `ForfeitRewardAdded` to remaining long-term stakers — a theft of unclaimed yield from the pool's intended beneficiaries (the loyal, still-locked stakers), matching **High – Theft of unclaimed yield**.

### Likelihood Explanation
This requires no special capital, timing, or crafted array of `_rewardTokens` — it triggers on every ordinary call to `getRewards`/`getReward` for every account, every time, because the missing call to `getRewardablePercentWAD` makes the forfeit calculation return `0` unconditionally. It is 100% reproducible and requires nothing beyond a normal stake → `startUnlock` → `getRewards` sequence, well within reach of any unprivileged actor.

### Recommendation
Fix `_calExpireForfeit` in `rewards/mWOMSVBaseRewarder.sol` to mirror `vlMGPBaseRewarder`:
```solidity
uint256 rewardablePercentWAD = mWOMSV.getRewardablePercentWAD(_account);
uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
```
so forfeiture is actually computed from the account's real lock/cooldown state instead of being hardcoded to `_amount`.

### Proof of Concept
1. Deploy `mWomSV`, `MasterMagpie`, and `mWOMSVBaseRewarder`; register a reward token and queue rewards via `queueNewRewards`.
2. Have an account lock mWOM via `mWomSV.lock`, accrue reward via `rewardPerToken` increase (e.g., another `queueNewRewards` call).
3. Call `mWomSV.startUnlock(fullAmount)` to move the account fully into cooldown (so `getRewardablePercentWAD` would be `< 1e18` if fixed, or exactly `1e18` per the question's premise — irrelevant since the current code never reads it).
4. Immediately call `MasterMagpie.multiclaimFor`/`multiclaimSpec` (or directly `getRewards`) for the account's `mWomSV` staking token.
5. Assert: `calExpireForfeit(account, rewardToken)` returns `0`, and the user receives 100% of `earned(account, rewardToken)` with no `ForfeitRewardAdded` event emitted — confirming forfeiture never applies regardless of cooldown state.
6. Confirm `balanceOf(account)` still equals `IMasterMagpie.stakingInfo(stakingToken, account).staked` throughout (this invariant is not violated; the loss is confined to reward-token accounting).

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

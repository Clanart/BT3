### Title
Users who forget to call `unlock()` after their cool-down period ends progressively forfeit earned rewards to other stakers - ([File: VLMGP.sol])

### Summary
`VLMGP.getRewardablePercentWAD()` computes a decaying "rewardable" weight for any unlock slot whose cool-down has already finished but whose owner has not yet called `unlock()` to withdraw. The decay factor shrinks toward zero the longer the user waits, so `vlMGPBaseRewarder._calExpireForfeit()` treats almost the entire proportional reward tied to that stale, still-escrowed balance as "forfeited." The forfeited amount is not returned to the user — it is redistributed to all other active stakers via `_queueNewRewardsWithoutTransfer()`. This is the same bug class as the referenced Nouns fork-escrow report: an inattentive holder whose principal is still locked in the contract (comparable to the unclaimed escrowed treasury share) permanently loses value to other, more attentive participants simply for failing to submit a timely follow-up transaction.

### Finding Description
When a user starts an unlock via `startUnlock()`, an unlock slot is created with `startTime`/`endTime` and `amountInCoolDown`: [1](#0-0) 

Once `block.timestamp > endTime` but the user has not yet called `unlock()` to actually withdraw the MGP, `getRewardablePercentWAD()` still counts this stale slot, but with a decaying formula: [2](#0-1) 

Specifically, for a fully-unlocked-but-unwithdrawn slot the contributed weight is:
```
percent += amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime)
```
As `timeNow` grows without the user calling `unlock()`, this term tends to zero, meaning the user's overall `rewardablePercentWAD` converges to only the share represented by tokens that are still actively locked (`fullyInLock`), effectively excluding almost the entire cool-down amount from "rewardable" status — even though that MGP is still sitting escrowed in the contract, still counted in `totalSupply()`/`balanceOf()`, and still generating reward accrual for the pool.

This value is consumed directly in the reward payout path: [3](#0-2) 

`_sendReward()` and `queueMGP()` then take whatever `_calExpireForfeit()` marks as non-rewardable and permanently reroute it to all other current stakers instead of the original owner: [4](#0-3) [5](#0-4) 

The equivalent mirrored logic exists for the `mWomSV`/`mWOMSVBaseRewarder` pair, so the same issue applies to mWOM stakers.

The root cause mirrors the Nouns fork-escrow report exactly: a user's principal balance is still fully accounted for by the protocol (`totalSupply()`/`balanceOf()` never decreases just because a cool-down slot expired unclaimed), but the *yield entitlement* calculation silently strips that balance's rightful share and hands it to other participants purely as a function of elapsed time and inaction, with no cap or floor protecting the inattentive user's legitimate proportional yield.

### Impact Explanation
Any staker who locks MGP (or mWOM), starts an unlock, and then simply forgets — or is unable — to submit the follow-up `unlock()` transaction promptly after the cool-down ends will see an ever-growing fraction of their earned vlMGP/mWomSV rewards permanently redirected to other stakers. Since MGP itself is still worth real value and reward tokens (including MGP harvested via `queueMGP`) are transferred away in `_sendReward`/`queueMGP`, this is a direct, permanent loss of yield for the inattentive user and a direct gain for other, active stakers — matching the "theft or permanent freezing of unclaimed yield" impact category.

### Likelihood Explanation
Low likelihood but plausible: any staker who does not immediately call `unlock()` right when their cool-down ends (e.g., they wait days/weeks before withdrawing, which is common user behavior) is affected — no special conditions or privileged actions are required, only ordinary wallet transactions (`lock`, `startUnlock`) and a period of inaction, matching "low likelihood + high impact = medium severity" as in the source report.

### Recommendation
Do not let the "rewardable" weight of an unlock slot decay toward zero merely because the user has not yet submitted the `unlock()` withdrawal transaction. Once a slot is past `endTime`, either continue treating the `amountInCoolDown` as fully rewardable (since the tokens are still escrowed in the contract) until actually withdrawn, or otherwise ensure any forfeited amount is credited back to the specific user rather than socialized to other stakers — analogous to the Nouns fix of including unclaimed amounts in `adjustedTotalSupply()`.

### Proof of Concept
1. Alice calls `VLMGP.lock(1000e18)` to lock 1000 MGP.
2. Alice calls `startUnlock(1000e18)`, creating an unlock slot with `startTime = T0`, `endTime = T0 + coolDownInSecs`.
3. Time passes beyond `endTime`, but Alice does not call `unlock()` (e.g., she is inactive for 60 days after cooldown ends).
4. Reward accrual continues normally in `vlMGPBaseRewarder`/`MasterMagpie` based on Alice's full `balanceOf` (unaffected by cooldown status): [6](#0-5) .
5. When Alice's rewards are computed via `getRewardablePercentWAD(Alice)`, the decaying branch at [7](#0-6)  causes her rewardable percent for that 1000 MGP slot to shrink close to 0 as `(timeNow - startTime)` grows.
6. When someone triggers `getReward`/`getRewards`/`queueMGP` for Alice, `_calExpireForfeit` computes a forfeit amount close to the entire proportional reward for that 1000 MGP share, and `_queueNewRewardsWithoutTransfer` redistributes it to all currently active vlMGP stakers — Alice's principal is unaffected but she permanently loses reward yield to other users purely because she delayed calling `unlock()`.

### Citations

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

**File:** VLMGP.sol (L292-306)
```text
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
```

**File:** rewards/vlMGPBaseRewarder.sol (L145-148)
```text
    function balanceOf(address _account) public override view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L331-347)
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
                (_amountReward * 10**vlMGPDecimal) / totalStaked();
        }
        emit ForfeitRewardAdded(_amountReward, _rewardToken);
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L363-377)
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

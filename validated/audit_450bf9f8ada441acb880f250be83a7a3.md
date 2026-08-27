### Title
`_calExpireForfeit()` in mWOMSVBaseRewarder never forfeits early-unlock rewards, letting users bypass the loyalty penalty and drain the reward pool meant for full-term stakers - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
The Twav report describes a broken conditional check whose logic silently fails on a legitimate input, causing the function to produce a materially wrong result (zero instead of a real value) that feeds directly into fund-moving logic (`buy()`/`sell()`). The same bug class — a forfeit/penalty computation whose core condition is effectively dead so it always yields the "no penalty" branch — exists in `mWOMSVBaseRewarder._calExpireForfeit()`, which feeds directly into `_sendReward()`, a fund-transferring function reachable by any staker.

### Finding Description
`mWOMSVBaseRewarder._calExpireForfeit()` is supposed to mirror `vlMGPBaseRewarder._calExpireForfeit()`, which computes the forfeitable share of a user's reward based on how much of their `mWomSV`/`vlMGP` is still actually locked vs. in cooldown, via `vlMGP.getRewardablePercentWAD(_account)`: [1](#0-0) 

But the `mWomSV` variant never calls the analogous `mWomSV.getRewardablePercentWAD(_account)` (which does exist on `mWomSV.sol`, mirroring `VLMGP.sol`'s implementation). Instead it initializes `rewardableAmount` to `_amount` and immediately computes `forfeitAmount = _amount - rewardableAmount`, which is unconditionally `0`: [2](#0-1) 

The `if (rewardableAmount > _amount) revert` check can never trigger, since the two values are always equal by construction — this is the "if condition missing the actual edge case" pattern from the source report: the check is present but structurally incapable of catching the real condition it's meant to guard (a user who is still partway through cooldown and should forfeit a pro-rated portion of their pending reward).

This result is consumed directly by `_sendReward()`, which is called from `getReward()`/`getRewards()` — both externally callable via `MasterMagpie` on behalf of any unprivileged staker: [3](#0-2) 

### Impact Explanation
Because `forfeitAmount` is always `0`, users who initiate `startUnlock()` on their `mWomSV` position and are still within the cooldown window (not yet eligible for full rewards under the design intent expressed by `getRewardablePercentWAD`) nonetheless receive their **full** pending reward from `mWOMSVBaseRewarder`, with nothing routed back into the reward pool via `_queueNewRewardsWithoutTransfer`. This is a direct, unprivileged-wallet-reachable loss of protocol-intended forfeited yield: the pool of "forfeited" rewards that should accrue to remaining long-term stakers via `ForfeitRewardAdded` never materializes for this reward stream, permanently freezing/misallocating that yield away from patient stakers to early-cooldown stakers. This matches theft/permanent freezing of unclaimed yield.

### Likelihood Explanation
High likelihood: any holder of `mWomSV` can trigger this simply by calling `startUnlock()` and then `getReward()`/`multiclaim` before their cooldown ends — no special conditions, no privileged role, and no reliance on admin behavior. It occurs on the normal reward-claim path used constantly by end users.

### Recommendation
Mirror `vlMGPBaseRewarder._calExpireForfeit()`: call `mWomSV.getRewardablePercentWAD(_account)` in `mWOMSVBaseRewarder._calExpireForfeit()` and compute `rewardableAmount = _amount * rewardablePercentWAD / 1e18` before deriving `forfeitAmount`, so that the forfeiture logic actually reflects a user's real lock/cooldown state instead of being a permanent no-op.

### Proof of Concept
1. User stakes into `mWomSV`, accrues pending `mWOMSVBaseRewarder` rewards over time.
2. User calls `mWomSV.startUnlock()` for their full balance, entering cooldown (`endTime = block.timestamp + coolDownInSecs`) — per `getRewardablePercentWAD`, this should reduce their rewardable share for time still in cooldown.
3. Before `endTime`, user calls `MasterMagpie.multiclaim`/`getReward()`, which routes to `mWOMSVBaseRewarder._sendReward()` → `_calExpireForfeit()`.
4. `_calExpireForfeit()` returns `forfeitAmount = 0` unconditionally (per the code cited above), so the user receives 100% of `userRewards[...]` regardless of their cooldown-reduced eligibility, and no `ForfeitRewardAdded` amount is ever queued back for other stakers. [2](#0-1)

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

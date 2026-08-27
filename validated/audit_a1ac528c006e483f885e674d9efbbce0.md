### Title
`mWOMSVBaseRewarder._calExpireForfeit` never applies `getRewardablePercentWAD`, letting mid-cooldown lockers claim 100% of rewards - ([File: rewards/mWOMSVBaseRewarder.sol])

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` (lines 385-398) hardcodes `rewardableAmount = _amount`, meaning `forfeitAmount` is always `0` regardless of the caller's lock/cooldown state. This is a straightforward divergence from the sibling contract `vlMGPBaseRewarder._calExpireForfeit`, which correctly multiplies `_amount` by `vlMGP.getRewardablePercentWAD(_account)` to force partial forfeiture from users mid-cooldown. As a result, `mWomSV` stakers who call `startUnlock` and are mid-cooldown keep 100% of their accrued reward share instead of forfeiting a portion to full lockers.

### Finding Description
`mWOMSVBaseRewarder.getReward` is `onlyMasterMagpie`-gated [1](#0-0) , and is reachable by any unprivileged staker through `MasterMagpie.multiclaimFor`, which iterates registered rewarders and invokes `getReward`/`getRewards` on behalf of the caller/receiver. Inside `getReward` → `_sendReward` → `_calExpireForfeit`, the forfeit computation is: [2](#0-1) 

Note `rewardableAmount = _amount` is set unconditionally before the `if (rewardableAmount > _amount)` check (which can never trigger), so `forfeitAmount = _amount - rewardableAmount` is always `0`. Compare with `vlMGPBaseRewarder._calExpireForfeit`, the parallel implementation for `vlMGP`: [3](#0-2) 

which properly queries `vlMGP.getRewardablePercentWAD(_account)` to scale down the rewardable amount for accounts mid-cooldown, and routes the forfeited portion back into the reward pool via `_queueNewRewardsWithoutTransfer` for other honest lockers [4](#0-3) .

`mWomSV.sol` does define an analogous `getRewardablePercentWAD` and `startUnlock` cooldown mechanism (confirmed present, matching `VLMGP.sol`'s pattern), but `mWOMSVBaseRewarder` never calls it. This means the "partial lockers forfeit to full lockers" invariant that the protocol implements for `vlMGP` is absent for `mWomSV`, letting any staker start unlocking (cooldown) on their `mWomSV` balance and still claim their full, undiscounted reward share via `multiclaimFor` before/while `endTime` is reached.

No modifier, `nonReentrant`, or accounting elsewhere compensates for this — `onlyMasterMagpie` and `updateReward` only gate caller identity and reward-index bookkeeping, they do not reintroduce the forfeit logic.

### Impact Explanation
This is a theft/misappropriation of unclaimed yield: full lockers (who keep 100% of their `mWomSV` locked) are entitled to receive forfeited shares from partial/cooldown lockers, but that mechanism is silently disabled for `mWomSV` rewards. Any staker can enter cooldown and still extract their entire reward share, permanently diverting yield that should have accrued to long-term lockers. This matches the "theft of unclaimed yield" impact class.

### Likelihood Explanation
Trivial and fully repeatable: any unprivileged `mWomSV` holder simply calls `startUnlock` on some or all of their balance and then calls `MasterMagpie.multiclaimFor` (or the rewarder's `getReward` through the normal claim path) to receive full rewards. No special capital, timing, front-running, or privileged role is required — it works on every claim, for every user, at all times mid-cooldown.

### Recommendation
Mirror `vlMGPBaseRewarder._calExpireForfeit`: modify `mWOMSVBaseRewarder._calExpireForfeit` to call `mWomSV.getRewardablePercentWAD(_account)` and scale `rewardableAmount = _amount * rewardablePercentWAD / 1e18` before computing `forfeitAmount`, so mid-cooldown stakers correctly forfeit the un-rewardable portion back into the pool via `_queueNewRewardsWithoutTransfer`.

### Proof of Concept
Foundry test plan:
1. Deploy `mWomSV`, `mWOMSVBaseRewarder`, `MasterMagpie`, and a reward token; register the rewarder and reward token.
2. Two users, A and B, each stake equal `mWomSV` balances.
3. Manager calls `queueNewRewards` to distribute reward tokens proportional to `totalStaked()`.
4. User A calls `mWomSV.startUnlock` on 90% of their balance (entering cooldown, `endTime` not yet reached).
5. Manager calls `queueNewRewards` again to accrue more rewards.
6. Both A and B call `MasterMagpie.multiclaimFor` to trigger `getReward` on the rewarder.
7. Assert: A receives 100% of their pro-rata share (via `RewardPaid` event / balance delta) instead of the expected reduced amount scaled by `mWomSV.getRewardablePercentWAD(A)`; assert `ForfeitRewardAdded` is never emitted despite A being mid-cooldown, confirming `forfeitAmount == 0` unconditionally.

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L233-247)
```text
    function getReward(address _account, address _receiver)
        public
        onlyMasterMagpie
        updateReward(_account)
        returns (bool)
    {
        uint256 length = rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            _sendReward(rewardToken, _account, _receiver);
        }

        return true;
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

**File:** rewards/vlMGPBaseRewarder.sol (L386-390)
```text
    function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
        uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
        uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
        if (rewardableAmount > _amount)
            revert InvalidRewardableAmount();
```

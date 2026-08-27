### Title
Reward forfeit is fully erased for accounts still inside their unlock cooldown window - ([File: rewards/vlMGPBaseRewarder.sol])

### Summary
`_calExpireForfeit()` scales the pending reward by `vlMGP.getRewardablePercentWAD(_account)`, but `VLMGP.getRewardablePercentWAD()` returns exactly `1e18` for any account whose entire vlMGP position is a single cooldown slot that has not yet reached `endTime`. Consequently, any user who has begun unlocking (but not yet completed the cooldown) pays zero forfeit on `getRewards`/`getReward`, even though the forfeit mechanism exists specifically to penalize users who are exiting their lock commitment.

### Finding Description
`_calExpireForfeit` computes:
```
uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
uint256 forfeitAmount = _amount - rewardableAmount;
``` [1](#0-0) 

`getRewardablePercentWAD` in `VLMGP.sol` computes a weighted percentage from the fully-locked amount plus each active cooldown slot:
```
percent = fullyInLock * 1e18 / userTotalVlmgp;
...
if (block.timestamp > userUnlocking[i].endTime) { // fully unlocked, decays with time
    percent += ...;
} else { // still in cool down
    percent += userUnlocking[i].amountInCoolDown * 1e18 / userTotalVlmgp;
}
``` [2](#0-1) 

When an account's entire vlMGP balance consists of a single, still-active cooldown slot (`fullyInLock == 0`, `userTotalVlmgp == amountInCoolDown`, and `block.timestamp <= endTime`), the "still in cool down" branch reduces to `percent = amountInCoolDown * 1e18 / amountInCoolDown = 1e18`. This holds for the entire cooldown duration, not just an instant — the decay logic only activates *after* `endTime` has passed (the "fully unlocked" branch), at which point the percent starts dropping proportional to elapsed time since expiry.

The consequence in `_sendReward`/`getReward`/`getRewards` is that `_calExpireForfeit` returns `0` for any account that is inside (not past) its cooldown window, regardless of how briefly it held the locked position before starting the unlock. Since reward accrual (`_earned`) is driven by `rewardPerTokenStored`, which is a running global index updated whenever `queueNewRewards`/`queueMGP` is called by the reward manager, an account can:
1. Lock MGP into vlMGP (permissionless action), briefly obtaining a non-zero `balanceOf` in `vlMGPBaseRewarder` (sourced from `IMasterMagpie.stakingInfo`).
2. Have a large `queueNewRewards`/`queueMGP` settlement occur (bumping `rewardPerTokenStored`), which credits the account with `earned = balance * Δindex` regardless of how long the account has held that balance.
3. Immediately start unlocking (enter cooldown).
4. Call `getRewards`/`getReward` while still inside the cooldown window (`block.timestamp <= endTime`), at which point `_calExpireForfeit` returns `0` because `getRewardablePercentWAD` is still `1e18`.

Existing checks do not prevent this: `getReward`/`getRewards` only gate on `onlyMasterMagpie`/`updateReward`, and neither checks how long the position has been held nor differentiates "still cooling down" (full weight) from "position never truly committed."

### Impact Explanation
This allows a user to capture the full amount of a large reward settlement while forfeiting nothing, despite the forfeit mechanism being designed specifically to slash rewards for users who exit their lock instead of remaining committed. Depending on reward-token size and settlement cadence, this is a repeatable mechanism for extracting yield that should otherwise be partially forfeited back into the pool (`_queueNewRewardsWithoutTransfer`), matching "Theft of unclaimed yield" (High). Note that the `totalStaked() == IERC20(vlMGP).totalSupply()` framing in the question is not itself a meaningful invariant to test, since `vlMGPBaseRewarder.totalStaked()` is implemented as a direct pass-through to `vlMGP.totalSupply()` [3](#0-2)  — it is tautologically always equal and cannot be "broken" by this exploit. The real, verifiable impact is the zero-forfeit payout during cooldown, independent of that invariant.

### Likelihood Explanation
Locking vlMGP and initiating an unlock are both permissionless actions available to any MGP holder; no privileged role is required. The exploit requires the attacker to time their lock/unlock around a known large reward settlement (predictable if `queueMGP`/`queueNewRewards` calls are visible in the mempool or occur on a known schedule), and capital equal to the MGP needed to lock for capturing a meaningful share of `rewardPerTokenStored`. This is repeatable for every large settlement.

### Recommendation
`getRewardablePercentWAD` (or the forfeit calculation) should not treat an account fully inside its cooldown as 100% rewardable. Instead, the rewardable percentage for an active (not-yet-expired) cooldown slot should decay based on how much of the *original lock duration* was actually served versus how much remains, or the forfeit calc should track/require a minimum holding duration relative to reward accrual timestamps, so that reward claimed against a given settlement reflects the lock commitment actually held at that time rather than the instant the account chooses to claim.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy/attach to `VLMGP`, `vlMGPBaseRewarder`, `MasterMagpie` fixtures with an MGP reward manager able to call `queueMGP`/`queueNewRewards`.
2. Attacker locks a large amount of MGP into vlMGP, registering non-zero `balanceOf` in the rewarder via `MasterMagpie.stakingInfo`.
3. Reward manager calls `queueNewRewards` with a large reward amount, bumping `rewardPerTokenStored`.
4. Attacker immediately calls `unlock`/`startUnlock` on `VLMGP` (entering cooldown, `fullyInLock` becomes 0 for that slot).
5. Attacker calls `getRewards(attacker, attacker, [rewardToken])` (via `MasterMagpie.multiclaimFor`) while `block.timestamp <= endTime` of the cooldown slot.
6. Assert: `vlMGP.getRewardablePercentWAD(attacker) == 1e18` at claim time, `calExpireForfeit(attacker, rewardToken) == 0`, and attacker receives the *full* `earned` amount with zero amount routed to `_queueNewRewardsWithoutTransfer`.
7. Compare against a control case where the attacker never locks/unlocks and simply holds a fully-locked position for the same duration — showing that a "just-passing-through" cooldown position pays out identically to a genuinely long-term locker, confirming the forfeit-avoidance.

### Citations

**File:** rewards/vlMGPBaseRewarder.sol (L137-139)
```text
    function totalStaked() public override view returns (uint256) {
        return IERC20(address(vlMGP)).totalSupply();
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

**File:** VLMGP.sol (L193-215)
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
```

## Title
Forfeiture evasion via `cancelUnlock` front-run of `getReward`/`queueMGP` due to point-in-time `getRewardablePercentWAD` check - (`rewards/vlMGPBaseRewarder.sol`, `VLMGP.sol`)

## Summary
`vlMGPBaseRewarder._calExpireForfeit` computes the forfeited portion of a user's bonus rewards/MGP by calling `vlMGP.getRewardablePercentWAD(_account)` at the moment of claim, rather than tracking time actually spent in cooldown per accrual period. Because `VLMGP.cancelUnlock` lets a user instantly zero out `amountInCoolDown` for a slot with no time restriction, an attacker can call `cancelUnlock` immediately before `getReward()`/`queueMGP` in the same transaction to make `getRewardablePercentWAD` report 100% and completely erase the forfeiture that should apply to rewards accrued while genuinely in cooldown.

## Finding Description
`VLMGP.getRewardablePercentWAD` [1](#0-0)  computes the rewardable fraction using the account's *current* `userUnlockings` state: any slot with `amountInCoolDown > 0` reduces the percent (fully if still in cooldown, or pro-rated if past `endTime`). This percent is fed into `vlMGPBaseRewarder._calExpireForfeit`, which is invoked live at claim time from both `_sendReward` (used by `getReward`/`getRewards`) and `queueMGP`: [2](#0-1) 

`VLMGP.cancelUnlock` has no cooldown-duration gate — it only checks that the slot is currently in cooldown, then unconditionally zeroes `amountInCoolDown` and decrements `totalAmountInCoolDown`: [3](#0-2) 

Exploit flow:
1. Attacker calls `startUnlock(largeAmount)` on a slot, moving a large portion of their vlMGP into `amountInCoolDown`, which drags `getRewardablePercentWAD` down and would normally cause a large forfeit to accrue against pending/future rewards while it sits there.
2. Reward accrual continues for the account while genuinely in cooldown (bonus tokens and MGP queued via `queueMGP`/`queueNewRewards` accumulate in `userRewards`/`rewardPerTokenStored`).
3. In the same transaction as the claim, attacker calls `cancelUnlock(_slotIndex)`, instantly setting `amountInCoolDown = 0` for that slot.
4. Attacker then calls `MasterMagpie.multiclaimFor` (or directly triggers `getReward`/`queueMGP`). `_calExpireForfeit` now reads `getRewardablePercentWAD == 1e18` because there is no more cooldown amount, so `forfeitAmount` collapses to 0 regardless of how long the tokens were actually in cooldown while the reward accrued.

Existing checks do not prevent this: `updateReward`/`updateRewards` modifiers and `onlyMasterMagpie`/`onlyManager` only gate *who* can trigger reward payout, not *when* relative to `cancelUnlock`; there is no snapshot of the forfeiture percent taken at the time rewards accrued, and `cancelUnlock` carries no cooldown/timelock of its own that would prevent it from being combined atomically with a claim.

## Impact Explanation
Forfeited amounts are redistributed to all vlMGP reward-pool participants via `_queueNewRewardsWithoutTransfer` [4](#0-3) . By evading forfeiture, the attacker keeps rewards that should have been redistributed to honest, fully-locked stakers — this is a direct theft of unclaimed yield from other participants and an economic exploit of the protocol's incentive design, repeatable on every claim cycle for any account with vlMGP locked and rewards accruing.

## Likelihood Explanation
Low capital and no special privileges are required — any vlMGP holder can call `startUnlock`, `cancelUnlock`, and trigger their own claim (`getReward`/`multiclaimFor`) as ordinary EOA/contract transactions, and the whole sequence (or at least `cancelUnlock` + claim) can be bundled into a single transaction/flash-bundle. There is no cost or delay imposed by `cancelUnlock` itself, so this is fully repeatable every time the attacker wants to claim.

## Recommendation
Do not compute forfeiture based on the account's *current* unlock state at claim time. Either (a) snapshot/accrue the forfeitable percentage continuously as rewards accrue (e.g., update `userRewards` split into forfeitable/non-forfeitable buckets whenever `rewardPerTokenStored` changes, using the percent at that time), or (b) impose a minimum lock/cooldown period on `cancelUnlock` (e.g., disallow canceling in the same block/transaction as a claim, or require the cancellation to itself go through a cooldown before it affects reward eligibility) so that `getRewardablePercentWAD` cannot be flipped atomically right before a payout.

## Proof of Concept
Foundry test plan:
1. Deploy `VLMGP`, `vlMGPBaseRewarder`, `MasterMagpie` (or minimal mocks reproducing `stakingInfo`), lock MGP for attacker.
2. Attacker calls `startUnlock(largeAmount)` to move most balance into cooldown.
3. Queue bonus rewards/MGP (`queueNewRewards`/`queueMGP`) while cooldown is active so `userRewards`/`rewardPerTokenStored` accrue against the low `getRewardablePercentWAD`.
4. **Baseline case:** call `getReward`/`multiclaimFor` directly without canceling — assert `forfeitAmount > 0` (matches `_amount * (1e18 - percent)/1e18`), and `ForfeitRewardAdded` event fired with nonzero amount.
5. **Exploit case:** in the same transaction, call `VLMGP.cancelUnlock(_slotIndex)` immediately before `multiclaimFor`/`getReward` — assert `forfeitAmount == 0` and the attacker receives the full `userRewards` amount, despite having spent the same accrual period in cooldown as the baseline case.
6. Compare payouts between the two scenarios to show the forfeit collapses to 0 solely due to the last-second `cancelUnlock` state flip, confirming the conservation invariant is broken.

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

**File:** VLMGP.sol (L339-349)
```text
    function cancelUnlock(uint256 _slotIndex) external override whenNotPaused {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        _checkInCoolDown(msg.sender, _slotIndex);

        totalAmountInCoolDown -= slot.amountInCoolDown; // reduce amount to cool down accordingly
        slot.amountInCoolDown = 0; // not in cool down anymore

        emit ReLock(msg.sender, _slotIndex, slot.amountInCoolDown);
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

**File:** rewards/vlMGPBaseRewarder.sol (L363-400)
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

    function _earned(address _account, address _rewardToken, uint256 _userVlmgpShare) internal view returns (uint256) {
        return ((_userVlmgpShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**vlMGPDecimal) + userRewards[_rewardToken][_account];
    }

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

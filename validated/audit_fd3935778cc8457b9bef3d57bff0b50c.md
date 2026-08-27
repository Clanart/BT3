### Title
Broken forfeiture calculation in `mWOMSVBaseRewarder._calExpireForfeit` allows unlockers to always claim full rewards with zero penalty - ([File: rewards/mWOMSVBaseRewarder.sol])

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` is supposed to penalize users who claim bonus rewards while not fully locked (i.e. in cooldown/early-unlock state) by forfeiting a portion of `_earned` rewards back to the pool. Instead, the function always sets `rewardableAmount = _amount`, making `forfeitAmount` unconditionally `0`. Combined with `mWomSV.startUnlock` calling `IMasterMagpie.multiclaimFor` before recording the new cooldown slot, any user (attacker or not) who starts an unlock — even repeatedly via `startUnlock`/`cancelUnlock` — claims 100% of accrued bonus rewards with no forfeiture ever applied.

### Finding Description
In `rewards/mWOMSVBaseRewarder.sol`, `_sendReward` computes the amount to forfeit via: [1](#0-0) 

which calls `_calExpireForfeit`: [2](#0-1) 

The function sets `rewardableAmount = _amount` on the very first line, so the immediately following check `if (rewardableAmount > _amount) revert(...)` can never trigger, and `forfeitAmount = _amount - rewardableAmount` is always `0`. This is clearly intended to scale `rewardableAmount` down using something like `mWOMSV.getRewardablePercentWAD(_account)` (which exists precisely for this purpose in `wombat/mWomSV.sol`) but that call/multiplication is missing, making the forfeiture path permanently dead code.

The reachable attacker path is via `mWomSV.startUnlock`, which triggers a full reward claim (`multiclaimFor`) before the new cooldown slot is even recorded: [3](#0-2) 

Since `getRewardablePercentWAD` (which is meant to reduce a user's "fully locked" credit while tokens are mid-cooldown) is never consulted by the rewarder's forfeiture logic, entering/exiting cooldown via `startUnlock`/`cancelUnlock` has zero economic consequence on reward capture — the user always receives the full `_earned` amount regardless of lock status.

Existing checks (`whenNotPaused`, `nonReentrant`, `onlyMasterMagpie` on `getReward`/`getRewards`) do not prevent this because the bug is in the pure reward-arithmetic function itself, not in access control or reentrancy.

### Impact Explanation
The intended design funnels forfeited rewards from early/partial unlockers back into the pool for remaining long-term lockers via `_queueNewRewardsWithoutTransfer` (called only when `forfeitAmount > 0`). Because `forfeitAmount` is always `0`, that redistribution never occurs, so honest long-term stakers permanently lose the forfeited-yield redistribution they were economically entitled to under the protocol's tokenomics — a theft/permanent loss of unclaimed yield for the remaining staker pool. This maps to Immunefi's "theft or permanent freezing of unclaimed yield" impact class.

### Likelihood Explanation
This requires no special privileges — any holder of locked `mWomSV`/`mMGP`-style tokens with accruing bonus rewards can trigger it simply by calling `startUnlock` (or any other path that reaches `getReward`/`getRewards`). No capital beyond normal locked position is needed, and the bug is deterministic and repeatable on every single claim, not just via startUnlock/cancelUnlock cycling — the cycling adds no additional benefit since forfeiture is already always zero for every claim path.

### Recommendation
Fix `_calExpireForfeit` to actually scale down `rewardableAmount` using the account's rewardable percentage (e.g., `ILocker(mWOMSV).getRewardablePercentWAD(_account)` multiplied against `_amount`), so that partially-locked/cooling-down balances forfeit the intended proportion of bonus rewards, and route the recovered `forfeitAmount` back into `rewardPerTokenStored` for remaining stakers as designed.

### Proof of Concept
Foundry test outline:
1. Deploy `mWomSV`, `mWOMSVBaseRewarder`, and mock `masterMagpie`; register a bonus reward token via `queueNewRewards`.
2. Lock tokens for user A (attacker) and user B (long-term holder), let bonus rewards accrue via `queueNewRewards`.
3. Call `mWomSV.startUnlock(smallAmount)` for A, which internally calls `multiclaimFor` → `mWOMSVBaseRewarder.getReward`.
4. Assert `RewardPaid` event amount equals full `earned()` amount (no `ForfeitRewardAdded` event emitted, i.e., `forfeitAmount == 0`) despite A being mid-cooldown.
5. Repeat `startUnlock`/`cancelUnlock` N times for A between reward accruals; assert cumulative claimed == cumulative theoretical `earned()` with zero forfeiture recorded across all cycles, and that `rewardPerTokenStored` for B never increases from A's forfeitures (since none occur), quantifying B's lost expected yield share.

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

**File:** wombat/mWomSV.sol (L247-277)
```text
    function startUnlock(uint256 _amountToCoolDown) external override whenNotPaused nonReentrant {
        if (_amountToCoolDown > getUserTotalLocked(msg.sender))
            revert NotEnoughLockedMWOM();

        uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
        address[] memory lps = new address[](1);
        address[][] memory mWomSVrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, mWomSVrewards, msg.sender);

        uint256 _slotIndex = getNextAvailableUnlockSlot(msg.sender);
        totalAmountInCoolDown += _amountToCoolDown;

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

        emit UnlockStarts(msg.sender, block.timestamp, _amountToCoolDown);
    }
```

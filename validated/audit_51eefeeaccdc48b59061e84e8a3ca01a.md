### Title
Unprivileged user can erase vlMGP reward forfeit by calling `cancelUnlock` before claiming, restoring 100% `rewardablePercentWAD` for rewards accrued during cooldown - ([File: rewards/vlMGPBaseRewarder.sol], [File: VLMGP.sol])

### Summary
`vlMGPBaseRewarder._calExpireForfeit` computes the forfeit percentage by calling `vlMGP.getRewardablePercentWAD(_account)` at claim time, which reads only the user's *current* lock/cooldown state, not the state that existed while the reward accrued. `VLMGP.cancelUnlock` lets a user unilaterally zero out a cooldown slot's `amountInCoolDown` without any penalty or reward settlement, instantly restoring `getUserTotalLocked` to 100% and thus `rewardablePercentWAD` to `1e18`. A user can therefore accrue rewards for the entire duration they were in cooldown, call `cancelUnlock` right before claiming, and pay zero forfeit on rewards that should have been (partially) forfeited.

### Finding Description
`_calExpireForfeit` in `rewards/vlMGPBaseRewarder.sol` (lines 386-400) computes:
```
uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
uint256 forfeitAmount = _amount - rewardableAmount;
``` [1](#0-0) 

`getRewardablePercentWAD` (VLMGP.sol lines 193-218) derives the forfeit-relevant percentage purely from the account's *current* `getUserTotalLocked`, `getUserAmountInCoolDown`, and the live `userUnlockings` slots — there is no snapshot of "percent locked at the time rewards accrued": [2](#0-1) 

`getUserTotalLocked` is computed live as `stakingInfo - getUserAmountInCoolDown` [3](#0-2) .

`cancelUnlock` allows the user to zero the cooldown slot at will, with only a boundary/cooldown-membership check, no reward claim, no penalty:
```
function cancelUnlock(uint256 _slotIndex) external override whenNotPaused {
    _checkIdexInBoundary(msg.sender, _slotIndex);
    UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];
    _checkInCoolDown(msg.sender, _slotIndex);
    totalAmountInCoolDown -= slot.amountInCoolDown;
    slot.amountInCoolDown = 0;
    emit ReLock(msg.sender, _slotIndex, slot.amountInCoolDown);
}
``` [4](#0-3) 

Contrast this with `startUnlock` and `unlock`, both of which force a `multiclaimFor` (settling/forfeiting rewards accrued up to that point) **before** mutating cooldown state [5](#0-4) . `cancelUnlock` has no such settlement step.

Exploit flow:
1. Attacker has vlMGP balance and calls `startUnlock` to put part of their stake into cooldown. From this point, `getRewardablePercentWAD` returns less than 100% for that account, so any `_sendReward`/`_calExpireForfeit` call during cooldown would forfeit part of the reward tied to the cooling-down share.
2. Attacker's `vlMGPBaseRewarder` accrues reward via `rewardPerToken` growth (from other users' `queueMGP`/`queueNewRewards` calls) while the attacker's tokens sit in `userRewards[token][attacker]` (via `_updateFor`) — this happens passively as global reward index moves, no explicit action needed from the attacker.
3. Immediately before calling `MasterMagpie.multiclaimFor`/`getReward`, attacker calls `VLMGP.cancelUnlock(slotIndex)`, zeroing `amountInCoolDown` for that slot with no penalty and no reward pre-claim.
4. `getUserAmountInCoolDown` becomes 0 for that slot, `getUserTotalLocked` returns to the full pre-lock amount, so `getRewardablePercentWAD` returns `1e18` (100%).
5. Attacker then calls `multiclaimFor`/`getReward`, and `_calExpireForfeit` computes `forfeitAmount = amount - amount*1e18/1e18 = 0`, sending the full historically-accrued reward with no forfeit, even though part of that reward accrued while the attacker was in cooldown and should have partially been forfeited into `_queueNewRewardsWithoutTransfer` for other stakers.

Existing modifiers (`whenNotPaused`, `nonReentrant` on some functions, `onlyMasterMagpie` on `getReward`) do not protect against this because `cancelUnlock` is a legitimate unprivileged user action and the forfeit calculation is not tied to a snapshot/checkpoint of cooldown history — it is entirely state-dependent at call time. `_checkInCoolDown` (VLMGP.sol) only checks the slot is currently in cooldown, not the reward-accrual history, so it does not prevent this front-running of the forfeit calculation.

### Impact Explanation
This is a theft of yield that should have been forfeited into the reward pool for other honest stakers (via `_queueNewRewardsWithoutTransfer`). Any reward that accrued to the attacker's cooling-down balance during the cooldown window and would have been subject to forfeit under `_calExpireForfeit` can be extracted at 0% forfeit by sandwiching the claim with `cancelUnlock`. This matches the "theft of unclaimed yield" impact class: value that should be redistributed to remaining lockers is instead captured entirely by the user who chose to unlock, defeating the purpose of the forfeit/penalty mechanism that funds `ForfeitRewardAdded`.

### Likelihood Explanation
- Requires no privileged role — any vlMGP holder with an active cooldown slot and any manager (or `queueMGP` caller) periodically adding rewards can trigger this.
- No capital beyond normal vlMGP holdings is needed; `cancelUnlock` costs only gas.
- Fully repeatable: a user can `startUnlock`, wait to accrue rewards, `cancelUnlock`, claim, and then `startUnlock` again for a new cooldown cycle, repeating indefinitely to always evade forfeit.
- The only constraint is that the attacker must remember/automate calling `cancelUnlock` immediately before `multiclaimFor`/`getReward`, which is trivial (can even be done via a single multicall/bundle in the same transaction/block since `cancelUnlock` has no `nonReentrant` restriction preventing a wrapper contract from sequencing both calls atomically).

### Recommendation
Decouple the forfeit calculation from the *current* live cooldown state:
- Settle/checkpoint rewards (call `getReward`/`_updateFor` and apply `_calExpireForfeit` against the state *before* any modification) whenever cooldown state changes, the same way `startUnlock`/`unlock` already force a `multiclaimFor` before mutating state — add an equivalent settlement call at the start of `cancelUnlock` before zeroing `amountInCoolDown`.
- Alternatively, base `getRewardablePercentWAD` on a time-weighted/checkpointed history of lock vs. cooldown state since the user's last reward checkpoint (`userRewardPerTokenPaid`), rather than only the instantaneous state at claim time, so that reward accrued during a cooldown window cannot retroactively be reclassified as "fully locked" by canceling the cooldown afterward.

### Proof of Concept
Foundry test plan:
1. Deploy `VLMGP`, `MasterMagpie`, `vlMGPBaseRewarder`, and a reward token; configure `vlMGP` in `vlMGPBaseRewarder` and register a `rewardManager`.
2. Two users, Alice and Bob, each `lock` equal amounts of MGP into `VLMGP`.
3. Alice calls `startUnlock(amount/2)` to place half her balance into cooldown (slot 0). Verify `getRewardablePercentWAD(alice) < 1e18`.
4. `rewardManager` calls `queueNewRewards`/`queueMGP` to inject reward tokens, increasing `rewardPerTokenStored`. Warp time forward (still within cooldown, `block.timestamp < slot.endTime`).
5. **Path A (no front-run):** Alice calls `MasterMagpie.multiclaimFor`/`getReward` directly. Assert `_sendReward` forfeits a nonzero amount (compare `calExpireForfeit(alice, token)` computed pre-call is > 0, and `ForfeitRewardAdded` event emitted with matching amount).
6. **Path B (front-run):** Reset state (or use Bob with identical accrual history). Bob calls `VLMGP.cancelUnlock(0)` immediately before `getReward`, then calls `getReward`. Assert `RewardPaid` amount for Bob equals full `userRewards` (no forfeit), and no `ForfeitRewardAdded` event fires, despite identical accrual history/timing as Alice.
7. Assert `toSend(Bob) > toSend(Alice)` for the same principal and accrual period, and that the amount difference equals the forfeit Alice paid — proving Bob (via `cancelUnlock` front-run) extracted yield that should have been forfeited.

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

**File:** VLMGP.sol (L125-129)
```text
    function getUserTotalLocked(address _user) override public view returns (uint256 _lockAmount) {
        // needs fixing
        (uint256 _amountInMasterMagpie, ) = IMasterMagpie(masterMagpie).stakingInfo(address(this), _user);
        _lockAmount = _amountInMasterMagpie - getUserAmountInCoolDown(_user);
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

**File:** VLMGP.sol (L275-336)
```text
    function startUnlock(uint256 _amountToCoolDown) external override whenNotPaused nonReentrant {
        if (_amountToCoolDown > getUserTotalLocked(msg.sender))
            revert NotEnoughLockedMPG();

        uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
        if (address(wombatBribeManager) != address(0) && 
            totalLockAfterStartUnlock < IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender))
            revert NotEnoughLockedMPG();

        address[] memory lps = new address[](1);
        address[][] memory vlMGPrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender);

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

        if (referralStorage != address(0)) IReferralStorage(referralStorage).updateTotalFactor(msg.sender);

        emit UnlockStarts(msg.sender, block.timestamp, _amountToCoolDown);
    }

    // @notice unlock a finished slot
    // @param slotIndex the index of the slot to unlock
    function unlock(uint256 _slotIndex) external override whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        if (slot.endTime > block.timestamp)
            revert StillInCoolDown();

        if (slot.amountInCoolDown == 0)
            revert UnlockedAlready();

        address[] memory lps = new address[](1);
        address[][] memory vlMGPrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender);

        uint256 unlockedAmount = slot.amountInCoolDown;
        _unlock(unlockedAmount);

        slot.amountInCoolDown = 0;
        IERC20(MGP).safeTransfer(msg.sender, unlockedAmount);

        emit Unlock(msg.sender, block.timestamp, unlockedAmount);
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

### Title
Tier-reward reset via mWomSV unlock allows repeated first-tier bonus extraction in `getRewardAmount`/`calDoubledCounted` - ([File: wombat/ArbWomUp3.sol])

### Summary
`calDoubledCounted` and `getRewardAmount` both derive the "already counted" base purely from the attacker-controlled, real-time value of `mWomSV.getUserTotalLocked(_account)` rather than from a persisted high-water-mark of rewards already paid. By fully unlocking mWomSV between two `incentiveDeposit(..., 2)` calls, an attacker resets their bracket to zero and re-earns the tapering reward-tier schedule's most generous (lowest-tier) multiplier on every fresh chunk instead of the intended marginal multiplier for cumulative deposits, extracting more total MGP than the tier design intends for the same aggregate WOM deposited.

### Finding Description
`incentiveDeposit` computes `rewardToSend = getRewardAmount(_amount, msg.sender, _mode==2)` before `_deposit` runs, so `mWomSV.getUserTotalLocked(_account)` reflects the user's pre-transaction locked balance [1](#0-0) . For lock mode, `getRewardAmount` computes `accumulated = _amountToConvert + mWomSV.getUserTotalLocked(_account)`, calculates the tiered `rewardAmount` for that accumulated total, and then subtracts `calDoubledCounted(_account)`, which independently recomputes the tiered reward using the *same* current `mWomSV.getUserTotalLocked(_account)` value [2](#0-1) .

Because both the "total" and the "already counted" figures read the same live, mutable `getUserTotalLocked` value instead of a persisted per-user "cumulative rewarded amount" counter, the subtraction always yields the correct marginal reward relative to *whatever the current locked balance happens to be at call time* — not relative to the amount the protocol has actually already rewarded historically. `mWomSV.unlock()` (via `startUnlock`/`unlock`) lets a user fully withdraw their locked mWom and reduce `getUserTotalLocked` back toward zero [3](#0-2) . Once reset, a subsequent `incentiveDeposit` treats the user as a brand-new, zero-balance locker, so `calDoubledCounted` returns near zero and the deposit's reward is computed against the lowest tier bracket again, rather than against the tier the user would occupy had they never unlocked.

Existing checks do not prevent this: `nonReentrant`/`whenNotPaused` only guard against reentrancy/pausing, not against sequential transactions; there is no persisted "amount already rewarded" state, no lockup on the reward itself, and no penalty tied specifically to reward recapture (mWomSV's cooldown only delays withdrawal of principal, it does not affect reward accounting in `ArbWomUp3`).

### Impact Explanation
Every time the attacker resets their `mWomSV` balance to zero and redeposits, they recapture the top-of-schedule (typically highest-multiplier, lowest-tier) reward rate instead of the correct marginal rate for their true cumulative position. Since `mgpReward` is paid from `IERC20(mgp).balanceOf(address(this))` (a shared, capped pool: `mgpleft`) [4](#0-3) , this results in the attacker draining more MGP (locked as vlMGP) than the tier design allots for their real deposited volume, at the expense of the shared reward pool available to other legitimate depositors — a quantifiable theft/drain of unclaimed vlMGP yield reserved for the protocol's incentive program.

### Likelihood Explanation
The attacker needs only: (1) enough WOM/capital to perform two (or more) `incentiveDeposit(..., 2)` calls, (2) ability to call `mWomSV.startUnlock` and, after `coolDownInSecs`, `mWomSV.unlock` on their own position — both are unprivileged, permissionless user functions [3](#0-2) . The only friction is waiting out `coolDownInSecs`, which is a time delay, not a capital or permission barrier, so the attack is fully repeatable by any unprivileged EOA over time.

### Recommendation
Track reward accounting with a persisted per-user monotonic "cumulative amount already rewarded" state variable (updated on each `incentiveDeposit`) instead of deriving the "already counted" base from the live, externally-reducible `mWomSV.getUserTotalLocked`. Alternatively, disallow/ignore any tier-bracket credit reduction caused by unlocking so that once a bracket has been rewarded it cannot be re-earned by a fresh deposit after unlock.

### Proof of Concept
Foundry test outline:
1. Deploy `ArbWomUp3` with a tapering `rewardMultiplier`/`rewardTier` schedule (e.g., tier0 multiplier > tier1 multiplier) and fund it with MGP.
2. Attacker calls `incentiveDeposit(A, ..., 2)`; record `mgpReward1` (vlMGP locked for attacker) and resulting `mWomSV.getUserTotalLocked(attacker)`.
3. Attacker calls `mWomSV.startUnlock(fullAmount)`, warps `coolDownInSecs`, then calls `mWomSV.unlock(slotIndex)` to fully reset `getUserTotalLocked(attacker)` to 0.
4. Attacker calls `incentiveDeposit(B, ..., 2)` again with fresh WOM; record `mgpReward2`.
5. Assert `mgpReward1 + mgpReward2 > getRewardAmount(A+B, attacker, true)` computed in one shot without the intermediate unlock — proving the split-and-unlock path yields strictly more MGP than the intended single-shot marginal calculation for the same total A+B deposited.

### Citations

**File:** wombat/ArbWomUp3.sol (L88-105)
```text
    function incentiveDeposit(
        uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode // 1 stake, 2 lock
    ) external _checkAmount(_amount) whenNotPaused nonReentrant {
        if (_amount == 0) return;
        
        uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender, _mode == 2);

        // giving out 50% more bonus
        if (_mode == 2)
            rewardToSend = rewardToSend * 2;

        _deposit(msg.sender, _convertRatio, _amount, _mode);

        IERC20(mgp).safeApprove(address(vlMGP), rewardToSend);
        vlMGP.lockFor(rewardToSend, msg.sender);
        // _bullMGP(rewardToSend, _minMGPRec, msg.sender);
        emit VLMGPRewarded(msg.sender, 0, rewardToSend);
    }
```

**File:** wombat/ArbWomUp3.sol (L107-144)
```text
    function getRewardAmount(uint256 _amountToConvert, address _account, bool _lock) external view returns (uint256) {
        uint256 mgpReward = 0;

        if (!_lock) {
            mgpReward = _amountToConvert * rewardMultiplier[getUserTier(_account)] / DENOMINATOR;
        } else {
            uint256 accumulated = _amountToConvert + mWomSV.getUserTotalLocked(_account);
            uint256 rewardAmount = 0;
            uint256 i = 1;

            while (i < rewardTier.length && accumulated > rewardTier[i]) {
                rewardAmount +=
                    (rewardTier[i] - rewardTier[i - 1]) *
                    rewardMultiplier[i - 1];
                i++;
            }
            rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1];
            mgpReward = (rewardAmount / DENOMINATOR) - calDoubledCounted(_account);
        }

        uint256 mgpleft = IERC20(mgp).balanceOf(address(this));
        return mgpReward > mgpleft ? mgpleft : mgpReward;
    }

    function calDoubledCounted(address _account) public view returns (uint256) {
        uint256 accuIn1 = mWomSV.getUserTotalLocked(_account);
        uint256 rewardAmount = 0;
        uint256 i = 1;
        while (i < rewardTier.length && accuIn1 > rewardTier[i]) {
            rewardAmount +=
                (rewardTier[i] - rewardTier[i - 1]) *
                rewardMultiplier[i - 1];
            i++;
        }

        rewardAmount += (accuIn1 - rewardTier[i - 1]) * rewardMultiplier[i - 1];
        return rewardAmount / DENOMINATOR;
    }    
```

**File:** wombat/mWomSV.sol (L247-303)
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
        address[][] memory mWomSVrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, mWomSVrewards, msg.sender);

        uint256 unlockedAmount = slot.amountInCoolDown;
        _unlock(unlockedAmount);

        slot.amountInCoolDown = 0;
        IERC20(mWOM).safeTransfer(msg.sender, unlockedAmount);

        emit Unlock(msg.sender, block.timestamp, unlockedAmount);
    }
```

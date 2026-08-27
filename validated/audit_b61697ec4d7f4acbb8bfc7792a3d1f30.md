### Title
VLMGP and mWomSV lock/unlock functions are fully gated by `whenNotPaused`, permanently freezing already-vested user funds while the contract is paused - (File: VLMGP.sol, wombat/mWomSV.sol)

### Summary
In `VLMGP.sol` and `wombat/mWomSV.sol`, every user-facing exit path from a lock (`startUnlock`, `unlock`, `cancelUnlock`, `forceUnLock`) carries the `whenNotPaused` modifier. Once the contract is paused, there is no path — analogous to `MasterMagpie.emergencyWithdraw`, which exists specifically for this situation — for a user to retrieve tokens that have already completed their cool-down period or to force-exit with a penalty. Retrieval is entirely gated behind the owner calling `unpause()`.

### Finding Description
`VLMGP.unlock` requires the cool-down `slot.endTime` to have passed before releasing tokens, but the function itself is `whenNotPaused`: [1](#0-0) 

The same restriction applies to `startUnlock`, `cancelUnlock`, and even the penalty-based emergency exit `forceUnLock`: [2](#0-1) 

The identical pattern exists in `mWomSV.sol`, where `unlock` and `cancelUnlock` are also `whenNotPaused` with no penalty-based or emergency alternative: [3](#0-2) 

This is directly analogous to the referenced Reserve Protocol finding: a legitimate, non-frozen exit condition exists (basket refreshed / cool-down elapsed) but the contract-level pause gate blocks the unprivileged user's transaction from reaching that exit condition, and only an owner action (`unpause()`) can restore it. Critically, the sibling contract `MasterMagpie.sol` demonstrates the intended mitigation for this exact scenario — it deliberately exposes `emergencyWithdraw`, marked `whenPaused` (the inverse gate), so users can retrieve their `available` balance specifically while the contract is paused: [4](#0-3) 

No such `whenPaused` escape hatch exists in `VLMGP.sol` or `mWomSV.sol`, so tokens that have already finished their cool-down (and are contractually owed to the user) are inaccessible for the entire duration the contract remains paused, with no alternate path for the depositor to reach their funds.

### Impact Explanation
Any user who has locked MGP (via `VLMGP`) or mWOM (via `mWomSV`) and has already started/finished the unlock cool-down cannot withdraw or force-unlock their tokens for as long as the contract is paused. Since pausing can persist indefinitely and there is no fallback function reachable by an ordinary wallet, this constitutes a permanent/indefinite freeze of already-vested user principal, satisfying the 24-hour-plus freezing-of-funds impact criterion.

### Likelihood Explanation
The path is trivially reachable: any wallet that has locked funds and completed (or is mid-way through) the cool-down period will hit this restriction the moment the contract enters the paused state — no special conditions or transaction ordering are required, only the pre-existing paused state that the contracts already support as normal operational functionality (e.g. maintenance/incident response pauses used elsewhere in the codebase, as evidenced by `MasterMagpie`'s and `WombatStaking`'s own pause/unpause admin functions).

### Recommendation
Add a `whenPaused`-gated emergency exit function to `VLMGP` and `mWomSV`, mirroring `MasterMagpie.emergencyWithdraw`, that allows a user to withdraw amounts already in a cooled-down/unlockable slot (or forfeited via the existing penalty formula) while the contract is paused, so users are never solely dependent on an owner's `unpause()` call to retrieve funds they are already entitled to.

### Proof of Concept
1. User calls `VLMGP.lock()` then `startUnlock()`, waits past `coolDownInSecs`.
2. Owner calls `pause()` (e.g., during a routine or incident-response pause) — see `VLMGP.sol` line 371 `pause() external onlyOwner`.
3. User calls `unlock(_slotIndex)` to claim their now-eligible tokens — this reverts due to `whenNotPaused` at `VLMGP.sol` line 315.
4. `forceUnLock` (line 352) and `cancelUnlock` (line 339) are equally blocked, so the user has zero avenues to recover tokens until the owner calls `unpause()`.
5. Contrast with `MasterMagpie.sol` lines 434-447, which explicitly provides `emergencyWithdraw` under `whenPaused` for the same class of situation — confirming this carve-out was a known necessary design pattern that was omitted in `VLMGP`/`mWomSV`.

### Citations

**File:** VLMGP.sol (L313-337)
```text
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
    }
```

**File:** VLMGP.sol (L339-367)
```text
    function cancelUnlock(uint256 _slotIndex) external override whenNotPaused {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        _checkInCoolDown(msg.sender, _slotIndex);

        totalAmountInCoolDown -= slot.amountInCoolDown; // reduce amount to cool down accordingly
        slot.amountInCoolDown = 0; // not in cool down anymore

        emit ReLock(msg.sender, _slotIndex, slot.amountInCoolDown);
    }

    // penalty caculation
    function forceUnLock(uint256 _slotIndex) external whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];
        _checkInCoolDown(msg.sender, _slotIndex);

        _unlock(slot.amountInCoolDown);
        (uint256 penaltyAmount, uint256 amountToUser) = expectedPenaltyAmount(_slotIndex);

        IERC20(MGP).safeTransfer(msg.sender, amountToUser);
        totalPenalty += penaltyAmount;

        slot.amountInCoolDown = 0;
        slot.endTime = block.timestamp;

        emit ForceUnLock(msg.sender, _slotIndex, amountToUser, penaltyAmount);
    }
```

**File:** wombat/mWomSV.sol (L279-315)
```text
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

    function cancelUnlock(uint256 _slotIndex) external override whenNotPaused {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        _checkInCoolDown(msg.sender, _slotIndex);

        totalAmountInCoolDown -= slot.amountInCoolDown; // reduce amount to cool down accordingly
        slot.amountInCoolDown = 0; // not in cool down anymore

        emit ReLock(msg.sender, _slotIndex, slot.amountInCoolDown);
    }
```

**File:** rewards/MasterMagpie.sol (L434-447)
```text
    /// @notice Withdraw all available tokens without caring about rewards. EMERGENCY ONLY. 
    ///         Locked Token can not be emergent withdraw.
    /// @param _stakingToken Staking token of the pool
    /// @dev withdrawFor of the rewarder with the third param at false is an emergency withdraw
    function emergencyWithdraw(address _stakingToken) external whenPaused {
        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        UserInfo storage user = userInfo[_stakingToken][msg.sender];
        uint256 availableaAmount = user.available;
        user.available = 0;
        IERC20(pool.stakingToken).safeTransfer(address(msg.sender), availableaAmount);
        emit EmergencyWithdraw(msg.sender, _stakingToken, availableaAmount);
        user.amount = user.amount - availableaAmount;
        user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12;
    }
```

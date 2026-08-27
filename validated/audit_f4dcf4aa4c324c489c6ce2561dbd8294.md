### Title
Permanently frozen forfeited MGP penalties due to missing setter for `penaltyDestination` in VLMGP - (File: VLMGP.sol)

### Summary
`VLMGP.sol` accumulates forfeited MGP from ordinary users calling `forceUnLock` into the `totalPenalty` accumulator, to be later swept out via `transferPenalty`. However, `transferPenalty` requires `penaltyDestination != address(0)`, and the admin-function block of the contract contains no function that sets `penaltyDestination` to a non-zero value, mirroring the exact root cause of the referenced report (a required non-zero destination address for pooled funds that the codebase never exposes a setter for).

### Finding Description
Any unprivileged wallet holding locked MGP can call `forceUnLock` on an in-cooldown slot, which computes a penalty and adds it to the contract-wide `totalPenalty` accumulator: [1](#0-0) 

The only way to remove this accumulated penalty from the contract is `transferPenalty`, which reverts unless `penaltyDestination` has been set to a non-zero address: [2](#0-1) 

The admin-function section of the contract (`pause`, `unpause`, `transferPenalty`, `setWhitelistForTransfer`, `setMasterChief`, `setCoolDownInSecs`, `setMaxSlots`) contains no `setPenaltyDestination` (or equivalent) function to initialize this address: [3](#0-2) 

This is structurally identical to the referenced Sherlock report: a variable gating the movement of pooled/forfeited funds (`invalidBridgedAmountsPool` in the bridge case, `penaltyDestination` here) that is required to be non-zero to unlock a transfer, but for which the contract exposes no setter, meaning it permanently remains `address(0)` and the guarded function (`transferPenalty` / `restoreBridgeTransaction`) always reverts.

### Impact Explanation
Since `penaltyDestination` can never be set away from `address(0)`, every call to `transferPenalty` will always revert, permanently trapping all MGP forfeited by ordinary users through `forceUnLock` inside the `VLMGP` contract. This is caused entirely by unprivileged-wallet activity (`forceUnLock`), and the penalty funds accumulate indefinitely with no code path to retrieve them — a permanent freezing of funds condition (well beyond 24 hours, effectively forever).

### Likelihood Explanation
Likelihood is high: `forceUnLock` is a normal, unprivileged user-facing function used whenever a user wants to exit a cooldown early, so `totalPenalty` will organically accumulate as part of expected protocol usage. No malicious or privileged action is required to trigger the freeze — it manifests as soon as any user forfeits a penalty and the admin later tries (and fails) to sweep it.

### Recommendation
Add an `onlyOwner` setter (e.g., `setPenaltyDestination(address _dest)`) to `VLMGP.sol` that updates `penaltyDestination`, mirroring the fix recommended for the referenced report (adding a setter for `invalidBridgedAmountsPool`). Ensure the setter emits the existing `PenaltyDestinationUpdated` pattern used elsewhere in the codebase (as seen declared in `wombat/mWomSV.sol`) for consistency and monitoring.

### Proof of Concept
1. Deploy/initialize `VLMGP` normally; `penaltyDestination` defaults to `address(0)` and no admin function exists to change it.
2. An ordinary user locks MGP, starts an unlock (`startUnlock`), and while still in cooldown calls `forceUnLock`, incurring a penalty added to `totalPenalty`: [1](#0-0) 
3. Owner (or anyone) calls `transferPenalty()` to sweep the accumulated penalty out of the contract: [2](#0-1) 
4. The call reverts with `PenaltyToNotSet()` since `penaltyDestination` is still `address(0)` and no function in the contract can ever change it, permanently locking the forfeited MGP inside `VLMGP`.

### Citations

**File:** VLMGP.sol (L352-367)
```text
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

**File:** VLMGP.sol (L369-420)
```text
    /* ============ Admin Functions ============ */

    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    function transferPenalty() external onlyOwner {
        if(penaltyDestination == address(0))
            revert PenaltyToNotSet();

        IERC20(MGP).safeTransfer(penaltyDestination, totalPenalty);

        emit PenaltySentTo(penaltyDestination, totalPenalty);

        totalPenalty = 0;
    }

    function setWhitelistForTransfer(address _for, bool _status) external onlyOwner {
        transferWhitelist[_for] = _status;

        emit WhitelistSet(_for, _status);
    }

    function setMasterChief(address _masterMagpie) external onlyOwner {
        if(_masterMagpie == address(0)) revert InvalidAddress();
        address oldChief = masterMagpie;
        masterMagpie = _masterMagpie;

        emit NewMasterChiefUpdated(oldChief, masterMagpie);
    }

    function setCoolDownInSecs(uint256 _coolDownSecs) external onlyOwner {
        if(_coolDownSecs <= 0) revert InvalidCoolDownPeriod();
        coolDownInSecs = _coolDownSecs;

        emit CoolDownInSecsUpdated(_coolDownSecs);
    }

    /// @notice Change the max number of unlocking slots
    /// @param _maxSlots the new max number
    function setMaxSlots(uint256 _maxSlots) external onlyOwner {
        if (_maxSlots <= maxSlot)
            revert MaxSlotCantLowered();

        maxSlot = _maxSlots;

        emit MaxSlotUpdated(maxSlot);
    }
```

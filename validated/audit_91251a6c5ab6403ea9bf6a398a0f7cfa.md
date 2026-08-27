### Title
Locked vlMGP can be permanently frozen if the lock recipient is a beneficiary contract without the ability to call `unlock`/`startUnlock`/`forceUnLock` - (File: VLMGP.sol)

### Summary
`VLMGP.lockFor()` allows any caller to lock MGP tokens on behalf of an arbitrary `_for` address [1](#0-0) . All of the functions that let a user act on that lock — `startUnlock`, `unlock`, `cancelUnlock`, and `forceUnLock` — are hard-coded to operate on `msg.sender`, with no "for"-style variant that lets a third party unlock/withdraw on behalf of the recipient [2](#0-1) [3](#0-2) . This mirrors the reported `TimeLock.claim()` bug class: assets are credited to a beneficiary address, but only that same address can invoke the withdrawal-initiating call.

### Finding Description
`lockFor` is unauthenticated and permissionless — any wallet can call it to lock MGP into `userUnlockings[_for]` for any `_for` address [1](#0-0) . The interface confirms there is no `unlockFor`, `startUnlockFor`, or `forceUnlockFor` counterpart — only `lock`, `startUnlock`, `cancelUnlock`, and `unlock` are exposed, and the last three all key off `msg.sender` [4](#0-3) [5](#0-4) .

This same `lockFor` path is exercised automatically by `MasterMagpie` when distributing MGP emissions for the "default" reward pool: `_sendVlMGPFor` calls `vlmgp.lockFor(_amount, _account)` where `_account` is the staker being paid [6](#0-5) , and this is reachable from the permissionless `multiclaimFor`/`multiclaimSpec` entry points that any wallet can trigger for any staking account [7](#0-6) .

If the recipient address (`_for`/`_account`) is an immutable smart contract with no built-in ability to call `startUnlock`/`unlock`/`forceUnLock` against `VLMGP` (analogous to the `TimeLock` beneficiary that cannot call `claim`), the MGP locked to that address can never be moved into cooldown or withdrawn. There is no owner/admin rescue path for a specific user's stuck lock — the only admin function touching balances is `transferPenalty`, which only sweeps the accumulated penalty pool, not a user's principal [8](#0-7) .

### Impact Explanation
Locked MGP credited to a contract-type beneficiary (whether it locked itself via a UI-triggered tx, or received the lock passively from `MasterMagpie`'s automatic reward-to-lock flow) becomes permanently unreachable, since the sole withdrawal-initiating functions are gated to `msg.sender == the locked account` and no alternative recovery mechanism exists. This is a permanent freeze of user funds.

### Likelihood Explanation
Any wallet can trigger the vulnerable path today without special privileges: `lockFor` is a public, unauthenticated entry point [9](#0-8) , and `multiclaimFor`/`multiclaimSpec` (also unauthenticated) cause `MasterMagpie` to lock MGP for arbitrary staking accounts via the same code path [7](#0-6) . Any account that is a contract without call-forwarding capability toward `VLMGP.unlock`/`startUnlock`/`forceUnLock` will be permanently unable to retrieve its locked MGP.

### Recommendation
Add `_for`/`onBehalfOf`-style variants of `startUnlock`, `unlock`, `cancelUnlock`, and `forceUnLock` that allow any caller to initiate/finalize unlocking on behalf of the recorded beneficiary and send the resulting MGP directly to that beneficiary, mirroring the recommendation in the referenced report to let any account trigger the beneficiary-restricted operation.

### Proof of Concept
1. Any wallet calls `VLMGP.lockFor(amount, contractX)` where `contractX` is an immutable contract (e.g., a minimal proxy with no fallback/delegatecall or arbitrary-call capability) [9](#0-8) .
2. `contractX`'s MGP balance now exists solely in `userUnlockings[contractX]`/locked balance inside `VLMGP`.
3. `contractX` cannot call `startUnlock`, `unlock`, `cancelUnlock`, or `forceUnLock` because these all read/write based on `msg.sender` [5](#0-4) [3](#0-2) , and it has no logic to invoke them.
4. There is no admin or third-party function to unlock/withdraw on `contractX`'s behalf, so the locked MGP is frozen permanently.

### Citations

**File:** VLMGP.sol (L260-268)
```text
    // @notice lock MGP in the contract
    // @param _amount the amount of MGP to lock
    // @param _for the address to lcock for
    // @dev the tokens will be taken from msg.sender
    function lockFor(uint256 _amount, address _for) override external whenNotPaused nonReentrant {
        _lock(msg.sender, _for, _amount);

        emit NewLock(_for, block.timestamp, _amount);
    }
```

**File:** VLMGP.sol (L275-337)
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
    }
```

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

**File:** VLMGP.sol (L379-388)
```text
    function transferPenalty() external onlyOwner {
        if(penaltyDestination == address(0))
            revert PenaltyToNotSet();

        IERC20(MGP).safeTransfer(penaltyDestination, totalPenalty);

        emit PenaltySentTo(penaltyDestination, totalPenalty);

        totalPenalty = 0;
    }
```

**File:** interfaces/IVLMGP.sol (L30-34)
```text
    function lock(uint256 _amount) external;
    function startUnlock(uint256 _amountToCoolDown) external;
    function cancelUnlock(uint256 _slotIndex) external;
    function unlock(uint256 slotIndex) external;
}
```

**File:** rewards/MasterMagpie.sol (L405-417)
```text
    /// @notice Claims for each of the pools with specified rewards to claim for each pool
    function multiclaimSpec(address[] calldata _stakingTokens, address[][] memory _rewardTokens)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, msg.sender, msg.sender, _rewardTokens);
    }

    /// @notice Claims for each of the pools with specified rewards to claim for each pool
    function multiclaimFor(address[] calldata _stakingTokens, address[][] memory _rewardTokens, address _account)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, _account, _account, _rewardTokens);
    }
```

**File:** rewards/MasterMagpie.sol (L652-657)
```text
    function _sendVlMGPFor(address _account, address _receiver, uint256 _amount) internal {
        IERC20(mgp).safeApprove(address(vlmgp), _amount);
        vlmgp.lockFor(_amount, _account);

        emit HarvestMGP(_account, _receiver, _amount, true);
    }
```

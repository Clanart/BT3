### Title
Unconditional reward-claim call inside `unlock()`/`startUnlock()` permanently freezes principal if any registered base rewarder reverts - (File: `VLMGP.sol`, `wombat/mWomSV.sol`, `rewards/MasterMagpie.sol`)

### Summary
`VLMGP.unlock()` and `mWomSV.unlock()` (as well as `startUnlock()`) unconditionally call `IMasterMagpie(masterMagpie).multiclaimFor(...)` before releasing a user's principal from a cool-down slot. This call fans out into `MasterMagpie._multiClaim` → `_claimBaseRewarder`, which claims from every base rewarder registered for that staking token. If any single reward-token payout in that chain becomes permanently unclaimable (e.g. reward math underflow/overflow, or a reward token transfer that can never succeed again), the entire `unlock()` transaction reverts forever, permanently freezing the user's principal MGP/mWOM — funds completely unrelated to the broken reward stream. This mirrors the `BalancedVault` bug class: a failure in one sub-component (here, one reward path) makes it impossible to retrieve unrelated locked capital (here, the vested/locked principal), with no partial or emergency-withdrawal path bypassing it.

### Finding Description
`VLMGP.unlock()` requires a successful reward claim before it will release cooled-down MGP: [1](#0-0) 

The same unconditional-claim-before-withdraw pattern exists in `startUnlock()`: [2](#0-1) 

And identically in `mWomSV.unlock()`: [3](#0-2) 

The claim call routes through `MasterMagpie.multiclaimFor` → `_multiClaim`, which iterates the pool's reward tokens and calls `_claimBaseRewarder` for each, with no per-token isolation (no try/catch, no skip-on-failure): [4](#0-3) [5](#0-4) 

Because the whole `_multiClaim` loop is a single atomic call and its success is a hard precondition for `unlock()`/`startUnlock()` to proceed, a permanent revert anywhere in the reward-claim path for the `vlmgp`/`mWomSV` staking-token pool (e.g. a base rewarder whose payout accounting becomes permanently broken, or whose reward-token balance can never satisfy the transfer) makes it impossible for any user to ever unlock or start unlocking their principal again — even though the principal itself (locked MGP/mWOM) is fully unrelated to the broken reward stream. There is no emergency/partial withdrawal path for locked/cooling-down principal that bypasses the reward claim (the `emergencyWithdraw` function in `MasterMagpie` only handles `user.available` for non-locked pools and explicitly excludes locked tokens): [6](#0-5) 

This is structurally analogous to the `BalancedVault` finding: coupling an unrelated, potentially-fragile sub-system (one market/one reward token) to the only withdrawal path for otherwise-healthy funds (other markets/the locked principal), with no fallback to cut losses and exit.

### Impact Explanation
If the reward-claim path for the vlMGP or mWomSV pool becomes permanently reverting, every user with MGP or mWOM locked or in cool-down is permanently unable to call `unlock()` (or `startUnlock()`), since both hard-require a successful `multiclaimFor` call first. This freezes 100% of locked/cooling-down principal in `VLMGP` and `mWomSV` indefinitely (well beyond 24 hours), with `forceUnLock` being the only alternative — and that function also depends on `_checkInCoolDown`/`_unlock` bookkeeping tied to the same slot state, not an independent claim-free exit for users who haven't started cool-down. Given the significant governance/yield capital typically locked in `vlMGP`, this is a protocol-wide, difficult-to-reverse loss of user access to funds.

### Likelihood Explanation
The trigger does not require any admin/governance action or external-protocol misbehavior: it only requires that the internal reward accounting/claim logic for the pool (`_claimBaseRewarder`/`BaseRewardPool` math) enters a state where a claim transaction reverts unconditionally for at least one user — a state reachable purely through normal, unprivileged usage of `lock`, `deposit`, and reward accrual over time (e.g., reward-per-share/debt arithmetic errors accumulating). Because `unlock()`/`startUnlock()` unconditionally depend on this same call succeeding for every affected user, once the condition is hit, it is systemic and cannot be worked around by an ordinary wallet.

### Recommendation
Decouple principal unlock/cool-down initiation from reward claiming: allow `unlock()` and `startUnlock()` to succeed and release principal even if the associated reward claim fails, e.g. by wrapping the `multiclaimFor` call in a try/catch (or by exposing a claim-free `unlockWithoutClaim` path), and/or by not requiring rewards to be claimed at all as a precondition for principal withdrawal. This restores an emergency-withdrawal-like guarantee that a broken reward stream for one token cannot forfeit access to unrelated locked capital.

### Proof of Concept
1. A user calls `VLMGP.lock()` and later `startUnlock()`, then waits out `coolDownInSecs`.
2. Independently, the base rewarder registered for the `vlmgp` staking-token pool in `MasterMagpie` enters a state where any further `claim`/payout for at least one user permanently reverts (e.g., due to reward-per-share accounting drift or reward-token balance shortfall in `BaseRewardPool`).
3. The user calls `VLMGP.unlock(slotIndex)`. This internally calls `IMasterMagpie(masterMagpie).multiclaimFor([address(this)], [...], msg.sender)`: [7](#0-6) 
4. `multiclaimFor` → `_multiClaim` → `_claimBaseRewarder` reverts for the broken reward path.
5. The entire `unlock()` call reverts, and the user's already-cooled-down MGP principal (`slot.amountInCoolDown`) can never be transferred out via `unlock()`, permanently freezing it, with no alternative claim-free withdrawal function available for cooled-down/locked principal.

### Citations

**File:** VLMGP.sol (L275-311)
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
```

**File:** VLMGP.sol (L315-336)
```text
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

**File:** wombat/mWomSV.sol (L279-303)
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
```

**File:** rewards/MasterMagpie.sol (L412-417)
```text
    /// @notice Claims for each of the pools with specified rewards to claim for each pool
    function multiclaimFor(address[] calldata _stakingTokens, address[][] memory _rewardTokens, address _account)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, _account, _account, _rewardTokens);
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

**File:** rewards/MasterMagpie.sol (L536-562)
```text
    function _multiClaim(address[] calldata _stakingTokens, address _user, address _receiver, address[][] memory _rewardTokens) internal nonReentrant {
        uint256 length = _stakingTokens.length;
        if (length != _rewardTokens.length) revert LengthMismatch();

        uint256 vlMGPPoolAmount;
        uint256 mWOmPoolAmount;
        uint256 defaultPoolAmount;

        for (uint256 i = 0; i < length; ++i) {
            address _stakingToken = _stakingTokens[i];
            UserInfo storage user = userInfo[_stakingToken][_user];
            
            updatePool(_stakingToken);
            uint256 claimableMgp = _calNewMGP(_stakingToken, _user) + unClaimedMgp[_stakingToken][_user];

            if (_stakingToken == address(vlmgp)) {
                vlMGPPoolAmount += claimableMgp;
            } else if (MPGRewardPool[_stakingToken]) {
                mWOmPoolAmount += claimableMgp;
            } else {
                defaultPoolAmount += claimableMgp;
            }

            unClaimedMgp[_stakingToken][_user] = 0;
            user.rewardDebt = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) / 1e12;
            _claimBaseRewarder(_stakingToken, _user, _receiver, _rewardTokens[i]);
        }
```

### Title
Full BaseRewardPool rewards continue to accrue on MGP amounts in cooldown because `VLMGP.startUnlock` never calls `MasterMagpie.withdrawVlMGPFor` - ([File: rewards/VLMGP.sol])

### Summary
`VLMGP.startUnlock` moves part of a user's locked MGP into a cooldown slot but never reduces the `UserInfo.amount` recorded in `MasterMagpie.userInfo[vlmgp][user]`. Since `BaseRewardPool.balanceOf` (used for `earned`/`getReward`) reads that raw `stakingInfo` amount instead of `VLMGP.getUserTotalLocked`, the cooling-down amount keeps earning full vlMGP-pool rewards even though it is no longer counted as "locked" by VLMGP's own accounting.

### Finding Description
`VLMGP.getUserTotalLocked` explicitly subtracts the cooldown amount from the raw `MasterMagpie.stakingInfo` value, and is even marked with a `// needs fixing` comment: [1](#0-0) 

However, `startUnlock` only records the cooldown slot and claims pending rewards; it never calls `MasterMagpie.withdrawVlMGPFor` to reduce `userInfo.amount`: [2](#0-1) 

The reduction of `userInfo.amount` in MasterMagpie only happens in `_unlock`, which is invoked from `unlock()`/`forceUnLock()` — not from `startUnlock()`: [3](#0-2) 

Meanwhile, `BaseRewardPool.balanceOf` (used for both `earned` and `getReward` for the vlMGP pool) reads `MasterMagpie.stakingInfo(stakingToken, _account)` directly, which is backed by the unmodified `userInfo.amount`, not `VLMGP.getUserTotalLocked`: [4](#0-3) 

Confirmed in `MasterMagpie`, `depositVlMGPFor`/`withdrawVlMGPFor` are the only entry points that mutate `userInfo.amount` for the vlMGP pool via `_deposit`/`_withdraw`: [5](#0-4) [6](#0-5) 

Because `startUnlock` calls neither of these, the exploit path is: `lock(100)` → `userInfo.amount = 100` → `startUnlock(50)` (adds cooldown slot, `userInfo.amount` still `100`) → `BaseRewardPool(vlMGP).getReward()` computes `earned` using `balanceOf(user) == 100`, i.e. still counting the 50 MGP that is sitting in cooldown and is no longer "locked" by VLMGP's own definition (`getUserTotalLocked` would report only 50). The attacker is therefore paid rewards on a non-productive, already-decaying stake, at the expense of the shared reward pool (diluting genuinely-locked stakers) — while `getUserTotalLocked`/`getRewardablePercentWAD` is what other subsystems (e.g. `WombatBribeManager.userTotalVotedInVlmgp` check in `startUnlock`) rely on for entitlement, creating a discrepancy between the two accounting views. I was not able to fully verify whether `vlMGPBaseRewarder.sol` (which exists in the repo and also references `totalStaked`) overrides `balanceOf` differently from the base `BaseRewardPool`; the grep only surfaced `totalStaked` overrides there, not `balanceOf`, so the vulnerable `balanceOf` implementation from `BaseRewardPool.sol` appears to be the one actually used, but this could not be conclusively confirmed within the available iterations.

None of `nonReentrant`, `whenNotPaused`, or the reward-index update (`updateReward`/`_updateFor`) prevent this, since they correctly update state relative to the (incorrect) `balanceOf` source — the bug is a source-of-truth mismatch between two different "locked amount" definitions, not a reentrancy or pause issue.

### Impact Explanation
This causes direct theft/misallocation of reward-pool funds (Immunefi "theft of unclaimed yield" / direct fund loss class): users who initiate cooldown continue to accrue and can claim full vlMGP `BaseRewardPool` rewards on the portion of their stake that is no longer genuinely locked, diluting rewards owed to legitimate long-term lockers and paying out MGP/other reward tokens that shouldn't be earned by the cooling-down balance.

### Likelihood Explanation
This requires no special privileges — any user who has locked MGP via `VLMGP.lock` can trigger it simply by calling `startUnlock` for part of their balance and then claiming rewards (`getReward`) repeatedly during the cooldown period (which can last up to `coolDownInSecs`, i.e., well over 24 hours). It is fully repeatable by any locker and scales with lock size, requiring only the capital already committed to locking.

### Recommendation
Make `startUnlock` immediately call `MasterMagpie.withdrawVlMGPFor(_amountToCoolDown, msg.sender)` (or otherwise synchronize `userInfo.amount`) so that `MasterMagpie.stakingInfo`/`BaseRewardPool.balanceOf` reflect only the truly-locked (non-cooldown) amount, matching `VLMGP.getUserTotalLocked`. Alternatively, change `BaseRewardPool.balanceOf` for the vlMGP pool to call `IVLMGP(stakingToken).getUserTotalLocked(_account)` instead of raw `stakingInfo`.

### Proof of Concept
Foundry test plan:
1. Deploy `MasterMagpie`, `VLMGP`, and `BaseRewardPool` wired together as in production (`VLMGP.masterMagpie` set, `MasterMagpie` pool for `vlmgp` registered with the `BaseRewardPool`).
2. Attacker locks `100e18` MGP via `VLMGP.lock(100e18)`.
3. Assert `BaseRewardPool.balanceOf(attacker) == 100e18` and `VLMGP.getUserTotalLocked(attacker) == 100e18`.
4. Queue rewards into the vlMGP `BaseRewardPool` (`queueNewRewards`).
5. Attacker calls `VLMGP.startUnlock(50e18)`.
6. Assert `VLMGP.getUserTotalLocked(attacker) == 50e18` (correctly reduced) but `BaseRewardPool.balanceOf(attacker) == 100e18` (unchanged — discrepancy of `50e18`).
7. Advance time; queue more rewards; call `BaseRewardPool.getReward(attacker, attacker)` via `MasterMagpie` and assert the reward paid corresponds to `balanceOf == 100e18`, i.e., strictly greater than what would be paid if computed against `getUserTotalLocked == 50e18` — demonstrating rewards earned on the cooling-down, non-productive 50e18.

### Citations

**File:** VLMGP.sol (L125-129)
```text
    function getUserTotalLocked(address _user) override public view returns (uint256 _lockAmount) {
        // needs fixing
        (uint256 _amountInMasterMagpie, ) = IMasterMagpie(masterMagpie).stakingInfo(address(this), _user);
        _lockAmount = _amountInMasterMagpie - getUserAmountInCoolDown(_user);
    }
```

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

**File:** VLMGP.sol (L455-459)
```text
    function _unlock(uint256 _unlockedAmount) internal {
        IMasterMagpie(masterMagpie).withdrawVlMGPFor(_unlockedAmount, msg.sender); // trigers update pool share, so happens before total amount reducing
        totalAmountInCoolDown -= _unlockedAmount;
        totalAmount -= _unlockedAmount;
    }
```

**File:** rewards/BaseRewardPool.sol (L130-185)
```text
    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }

    /// @notice Returns amount of reward token per staking tokens in pool
    /// @param _rewardToken Address reward token
    /// @return Returns amount of reward token per staking tokens in pool
    function rewardPerToken(address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return rewards[_rewardToken].rewardPerTokenStored;
    }

    function rewardTokenInfos()
        override
        external
        view
        returns
        (
            address[] memory bonusTokenAddresses,
            string[] memory bonusTokenSymbols
        )
    {
        uint256 rewardTokensLength = rewardTokens.length;
        bonusTokenAddresses = new address[](rewardTokensLength);
        bonusTokenSymbols = new string[](rewardTokensLength);
        for (uint256 i; i < rewardTokensLength; i++) {
            bonusTokenAddresses[i] = rewardTokens[i];
            bonusTokenSymbols[i] = IERC20Metadata(address(bonusTokenAddresses[i])).symbol();
        }
    }

    /// @notice Returns amount of reward token earned by a user
    /// @param _account Address account
    /// @param _rewardToken Address reward token
    /// @return Returns amount of reward token earned by a user
    function earned(address _account, address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return (
            (((balanceOf(_account) *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                (10**stakingDecimals())) + userRewards[_rewardToken][_account])
        );
    }
```

**File:** rewards/MasterMagpie.sol (L451-463)
```text
    function depositVlMGPFor(
        uint256 _amount,
        address _for
    ) external whenNotPaused _onlyVlMgp() {
        _deposit(address(vlmgp), _for, _amount, true);
    }
    
    function withdrawVlMGPFor(
        uint256 _amount,
        address _for
    ) external whenNotPaused _onlyVlMgp() {
        _withdraw(address(vlmgp), _for, _amount, true);
    }
```

**File:** rewards/MasterMagpie.sol (L482-505)
```text
    function _deposit(address _stakingToken, address _account, uint256 _amount, bool _isVlmgp) internal {
        updatePool(_stakingToken);

        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        UserInfo storage user = userInfo[_stakingToken][_account];

        if (user.amount > 0) {
            _harvestMGP(_stakingToken, _account);
        }
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount + _amount;
        if (!_isVlmgp) {
            user.available = user.available + _amount;
            IERC20(pool.stakingToken).safeTransferFrom(address(msg.sender), address(this), _amount);
        }
        user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12;

        if (_amount > 0)
            if (!_isVlmgp)
                emit Deposit(_account, _stakingToken, _amount);
            else
                emit DepositNotAvailable(_account, _stakingToken, _amount);
    }
```

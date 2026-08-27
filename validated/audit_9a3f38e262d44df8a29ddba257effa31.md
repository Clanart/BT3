Confirmed vulnerable path. `_deposit` calls `_harvestBaseRewarder` **before** updating `user.amount`, but the vlMGPBaseRewarder's `updateFor`/`_updateFor` computes `userVlMGPAmount = balanceOf(_account)` by reading `IMasterMagpie(masterMagpie).stakingInfo(...)` **live at call time**, not a snapshotted pre-deposit balance [1](#0-0) . Since `getReward`/`getRewards` is what a user calls to actually claim, and `startUnlock` invokes `multiclaimFor` which calls `_multiClaim` → `_claimBaseRewarder` → `rewarder.getReward` under `updateReward(_account)` modifier that calls `_updateFor(_account)` using **current** `balanceOf`, a freshly-deposited/locked balance captures full accrued `rewardPerToken` since the account's last checkpoint (0 if first interaction) [2](#0-1) [3](#0-2) .

### Title
Flash-loaned instantaneous-stake reward theft via unweighted `rewardPerToken` snapshot in `vlMGPBaseRewarder` - ([File: rewards/vlMGPBaseRewarder.sol])

### Summary
`vlMGPBaseRewarder._updateFor`/`updateReward` credits `userRewards[token][account]` using the account's **current** `balanceOf(account)` multiplied by the full delta between `rewardPerToken(token)` and the account's last-paid checkpoint, with no time-weighting or minimum holding period. An attacker can lock a large, flash-loaned MGP amount right before triggering a claim, capture a large slice of previously accrued (but not yet claimed) rewards, then immediately force-unlock to retrieve MGP and repay the loan.

### Finding Description
`VLMGP.lock`/`lockFor` transfers real MGP from `msg.sender` into VLMGP and calls `MasterMagpie.depositVlMGPFor` → `_deposit`, updating `userInfo[vlmgp][_for].amount` [4](#0-3) [5](#0-4) . `vlMGPBaseRewarder.balanceOf` reads this live amount via `stakingInfo` [6](#0-5) . `_updateFor`/`updateReward`/`updateRewards` compute `_earned` as `userVlMGPAmount * (rewardPerToken - userRewardPerTokenPaid[account]) / 1e18 + userRewards[account]` — for a brand-new account, `userRewardPerTokenPaid` is 0, so the full historical `rewardPerTokenStored` is multiplied against the newly-inflated balance in one shot [3](#0-2) . There is no vesting/time-lock requirement gating reward eligibility by stake duration.

The exit path exists: `startUnlock` sets a cooldown slot with `endTime = block.timestamp + coolDownInSecs` [7](#0-6) , and `forceUnLock` only requires `_checkInCoolDown`, i.e. `slot.amountInCoolDown != 0 && slot.endTime > block.timestamp` — both satisfied immediately in the same block right after `startUnlock` [8](#0-7) [9](#0-8) . `startUnlock` itself triggers `multiclaimFor` → `getReward`/`getRewards` (via `updateReward`/`updateRewards` modifiers), meaning attacker rewards get realized and paid out (`_sendReward`) as part of the same transaction sequence, before exiting [10](#0-9) [11](#0-10) .

Note: `forceUnLock`'s `expectedPenaltyAmount` applies a heavy principal penalty when unlocked instantly (elapsed time ≈ 0 gives `unlockFactor ≈ 0`, so `amountToUser = coolDownAmount/5`, i.e. 80% penalty on principal) [12](#0-11) . This penalty applies only to the locked principal being withdrawn early, not to the reward tokens already harvested via `getReward`, so the attacker still walks away with the full disproportionate reward-token payout while eating the MGP penalty — and if the reward-token value skimmed exceeds the 80% MGP penalty plus flash-loan fee, the attack is profitable for the attacker while permanently diluting legitimate stakers' yield.

### Impact Explanation
This is theft of unclaimed yield from long-term stakers: reward tokens queued via `queueNewRewards`/`_queueNewRewardsWithoutTransfer` accrue into `rewardPerTokenStored` over time for existing stakers, but a flash-inflated balance can claim a proportional (potentially majority, if attacker's flash-loaned balance dwarfs `totalStaked()`) share of that accrued pool in a single transaction. This matches "theft of unclaimed yield" impact class.

### Likelihood Explanation
Preconditions: MGP must be flash-loanable (or otherwise obtainable with large capital for a single block) and `vlMGPBaseRewarder.rewardPerTokenStored` must be non-zero (achieved via any prior `queueNewRewards` call, which is a normal, frequent operation for reward distribution). No privileged role is required — `lockFor`, `startUnlock`, `forceUnLock`, and the claim paths are all public/external and callable by any EOA/contract. The only friction is the 80% principal penalty on the flash-loaned MGP itself from `forceUnLock`, which bounds profitability to cases where skimmed reward-token value exceeds this penalty plus flash-loan fees; this is feasible when reward-token queued amounts are large relative to `totalStaked()`, e.g. right after a large `queueNewRewards` call.

### Recommendation
Time-weight reward accrual (e.g., checkpoint balance changes into a running weighted-average, or require a minimum lock duration/warm-up period before a newly increased balance is eligible for previously-accrued `rewardPerToken`). A minimal fix: on `depositVlMGPFor`/balance-increasing events, checkpoint `userRewardPerTokenPaid[token][account] = rewardPerToken(token)` for the *added* amount only, not retroactively for the full new balance against the pre-existing global index — or, simplest, disallow harvesting for an account within the same block/transaction that its lock balance was increased (freeze reward accrual on the incremental amount until the next `queueNewRewards` epoch boundary).

### Proof of Concept
Foundry test plan:
1. Deploy `MasterMagpie`, `VLMGP`, `vlMGPBaseRewarder`, MGP token, and a reward token; register vlMGP pool with the rewarder.
2. Have a "long-term staker" lock MGP via `VLMGP.lock` and let time pass.
3. Call `vlMGPBaseRewarder.queueNewRewards(rewardAmount, rewardToken)` to inflate `rewardPerTokenStored` while `totalStaked() > 0`.
4. In a single transaction (attacker contract): flash-borrow a large MGP amount, call `VLMGP.lockFor(flashAmount, attacker)`, then `VLMGP.startUnlock(flashAmount)` (which triggers `multiclaimFor`→`getReward`, crediting/paying attacker rewards proportional to `flashAmount`), then `VLMGP.forceUnLock(slotIndex)` to retrieve ~20% of `flashAmount` back, repay the flash loan (asserting shortfall covered by stolen reward-token value or reverting if not profitable to demonstrate the bug still improperly pays rewards regardless of net profitability).
5. Assert: `rewardToken.balanceOf(attacker)` after the sequence is `> 0` and proportional to `flashAmount / totalStaked()` fraction of `rewardAmount`, despite `attacker`'s lock duration being 0 seconds — comparing against the long-term staker's `earned()` before/after to show yield was diverted disproportionately to time held.

### Citations

**File:** rewards/vlMGPBaseRewarder.sol (L100-118)
```text
    modifier updateRewards(address _account, address[] memory _rewards) {
        uint256 length = _rewards.length;
        uint256 userVlMGPAmount = balanceOf(_account);
        
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewards[index];
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;

            userRewards[rewardToken][_account] = _earned(_account, rewardToken, userVlMGPAmount);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
        _;
    }

    modifier updateReward(address _account) {
        _updateFor(_account);
        _;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L145-148)
```text
    function balanceOf(address _account) public override view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L232-246)
```text
    function getReward(address _account, address _receiver)
        public
        onlyMasterMagpie
        updateReward(_account)
        returns (bool)
    {
        uint256 length = rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            _sendReward(rewardToken, _account, _receiver);
        }

        return true;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L349-361)
```text
    function _updateFor(address _account) internal {
        uint256 length = rewardTokens.length;
        uint256 userVlMGPAmount = balanceOf(_account);

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;

            userRewards[rewardToken][_account] = _earned(_account, rewardToken, userVlMGPAmount);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L379-384)
```text
    function _earned(address _account, address _rewardToken, uint256 _userVlmgpShare) internal view returns (uint256) {
        return ((_userVlmgpShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**vlMGPDecimal) + userRewards[_rewardToken][_account];
    }
```

**File:** VLMGP.sol (L234-248)
```text
    function expectedPenaltyAmount(uint256 _slotIndex) public view returns(uint256 penaltyAmount, uint256 amontToUser) {
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        uint256 coolDownAmount = slot.amountInCoolDown;
        uint256 baseAmountToUser = slot.amountInCoolDown / 5;
        uint256 waitingAmount = coolDownAmount - baseAmountToUser;

        uint256 unlockFactor = 1e12;
        if((block.timestamp - slot.startTime) <= (slot.endTime - slot.startTime))
            unlockFactor = ((block.timestamp - slot.startTime) * 1e12 / (slot.endTime - slot.startTime)) ** 2 / 1e12;

        uint256 unlockAmount = waitingAmount * unlockFactor / 1e12;
        amontToUser = baseAmountToUser + unlockAmount;
        penaltyAmount = coolDownAmount - amontToUser;
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

**File:** VLMGP.sol (L446-453)
```text
    function _checkInCoolDown(address _user, uint256 _slotIdx) internal view {
        UserUnlocking storage slot = userUnlockings[_user][_slotIdx];
        if (slot.amountInCoolDown == 0)
            revert UnlockedAlready();
            
        if(slot.endTime <= block.timestamp)
            revert NotInCoolDown();
    }
```

**File:** VLMGP.sol (L461-470)
```text
    function _lock(
        address spender,
        address _for,
        uint256 _amount
    ) internal {
        MGP.safeTransferFrom(spender, address(this), _amount);
        IMasterMagpie(masterMagpie).depositVlMGPFor(_amount, _for);
        totalAmount += _amount; // trigers update pool share, so happens after toal amount increase
        if (referralStorage != address(0)) IReferralStorage(referralStorage).updateTotalFactor(_for);
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

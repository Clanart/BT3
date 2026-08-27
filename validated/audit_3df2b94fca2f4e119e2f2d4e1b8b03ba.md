### Title
JIT reward-index sniping in `vlMGPBaseRewarder` allows an attacker to steal a disproportionate share of freshly-queued rewards by locking MGP right before `queueNewRewards` and immediately transitioning to cooldown - (File: rewards/vlMGPBaseRewarder.sol)

### Summary
`vlMGPBaseRewarder` distributes reward tokens using a global `rewardPerTokenStored` accumulator that is bumped instantly and in full by `queueNewRewards`, and each user's share is calculated using their *current* `balanceOf` snapshot rather than a time-weighted balance. Because `VLMGP.lock` increases a user's reward-relevant balance instantly, and `startUnlock` does not reduce that balance (cooldown amounts still count in `balanceOf` until final `unlock`), an attacker can front-run a large `queueNewRewards` call, capture the full reward-per-token bump on a large freshly-locked balance, and immediately start unlocking — all with no minimum holding period.

### Finding Description
`vlMGPBaseRewarder.balanceOf` reads the user's staked amount from `MasterMagpie.stakingInfo`, which reflects `user.amount` in `MasterMagpie` [1](#0-0) . `VLMGP._lock` calls `MasterMagpie.depositVlMGPFor`, which increases `user.amount` synchronously within the same transaction [2](#0-1) [3](#0-2) . In `_deposit`, the pre-existing balance is harvested (via `_harvestBaseRewarder`, which triggers `_updateFor`) **before** `user.amount` is incremented, so the attacker's `userRewardPerTokenPaid` checkpoint is set to whatever `rewardPerTokenStored` was *prior to* their large deposit [4](#0-3) .

When a manager subsequently calls `queueNewRewards`, the entire injected reward is distributed proportionally across `totalStaked()` in one atomic update: `rewardPerTokenStored += (_amountReward * 10**decimals) / totalStaked()` [5](#0-4) . There is no time-weighting, streaming, or vesting of this increase — the full bump applies instantly to every current holder regardless of how long they've held their balance.

The attacker's subsequent `getReward` or `startUnlock` call triggers `_updateFor`, which computes `_earned` as `userVlMGPAmount * (rewardPerToken_new - rewardPerTokenPaid_old) / 1e18` [6](#0-5) [7](#0-6) . Since `userVlMGPAmount` is the attacker's full freshly-locked balance and `userRewardPerTokenPaid` predates the reward injection, the attacker captures a share of the reward as if they had held that balance the entire accrual period.

Critically, `VLMGP.startUnlock` does not reduce the ERC20/`user.amount` balance used by `balanceOf` — it only records a cooldown schedule; the actual balance reduction happens later in `unlock`/`_unlock` via `withdrawVlMGPFor` [8](#0-7) [9](#0-8) . This means the attacker's reward-eligible balance stays at its post-lock size through the entire attack window, and `startUnlock` itself triggers a `multiclaimFor` harvest that crystallizes the sniped reward into `userRewards` before cooldown even begins [10](#0-9) .

The only anti-abuse mechanism in the rewarder, `_calExpireForfeit`/`getRewardablePercentWAD`, computes a penalty based on the ratio of locked vs. cooling-down amounts, not on holding duration. A user who is fully locked (not yet in cooldown) at the moment of harvest gets `rewardablePercentWAD = 100%`, i.e., **zero forfeit**, even though they may have held the position for only one block [11](#0-10) [12](#0-11) . None of `whenNotPaused`, `nonReentrant`, or `onlyManager` address this timing issue — they guard against reentrancy/pausing/unauthorized calls, not against instantaneous balance manipulation around a discrete reward-per-token bump.

### Impact Explanation
This is theft of unclaimed yield from genuine long-term lockers: reward tokens intended to be distributed pro-rata over the accrual period are instead captured disproportionately by whoever holds the largest balance at the exact block `queueNewRewards` executes. A well-capitalized attacker observing the mempool for a large `queueNewRewards` transaction can capture an outsized fraction of that distribution while holding capital for only the duration of one or two transactions, directly diluting the realized APR of honest long-term lockers. This matches Immunefi's "theft of unclaimed yield" impact class.

### Likelihood Explanation
The attack requires: (1) liquid MGP capital sufficient to dominate `totalStaked()` for the target reward token at the moment of injection, (2) mempool visibility of the `queueNewRewards` transaction (public/observable, called by a `rewardManager`), and (3) no special privileges — `lock`, `startUnlock`, and `getReward` are all public/external functions reachable by any EOA. This is feasible and repeatable each time a sizeable reward is queued (e.g., periodic bribe/incentive distributions), and does not depend on any admin misconfiguration — it is a structural property of the instantaneous, non-time-weighted `rewardPerTokenStored` accounting combined with `balanceOf` reflecting the full locked+cooldown amount.

### Recommendation
Introduce a minimum eligibility/holding delay before newly locked balance counts toward reward distributions (e.g., checkpoint new deposits so they only start accruing `rewardPerTokenStored` deltas from blocks after the deposit, or require a minimum lock duration before a balance is reward-eligible). Alternatively, stream `queueNewRewards` amounts linearly over a period (similar to Synthetix's `rewardRate`/`periodFinish` design) instead of applying the full amount atomically to `rewardPerTokenStored`, which removes the incentive to snipe a single block's reward injection.

### Proof of Concept
Foundry test plan:
1. Deploy `MasterMagpie`, `VLMGP`, `vlMGPBaseRewarder` with a reward token, and register a manager for `queueNewRewards`.
2. Setup: user `Alice` locks `1000 MGP` at `t=0` and holds for a long period (e.g., 30 days) accruing no rewards yet (baseline long-term holder).
3. At `t=30 days`, simulate mempool front-run:
   a. Attacker `Bob` calls `VLMGP.lock(100_000 MGP)` (much larger than Alice's stake) in block `N`.
   b. In the same or next block, manager calls `vlMGPBaseRewarder.queueNewRewards(rewardAmount, rewardToken)`.
   c. Immediately after, Bob calls `VLMGP.startUnlock(100_000)` (triggers `multiclaimFor` → harvest) then later `getReward`.
4. Assert: `Bob`'s captured reward ≈ `rewardAmount * 100_000 / (100_000 + 1000)` despite holding the position for only 1-2 blocks, while `Alice`, who held for 30 days, receives only `rewardAmount * 1000 / 101_000` — i.e., Bob's realized APR over his holding period vastly exceeds Alice's, and Alice's expected share (had reward been time-weighted) is diluted.
5. Assert `_calExpireForfeit(Bob, ...)` returns `forfeitAmount == 0` because `getRewardablePercentWAD(Bob) == 100%` at the harvest instant (fully locked, not in cooldown), confirming no penalty offsets the snipe. [5](#0-4) [2](#0-1)

### Citations

**File:** rewards/vlMGPBaseRewarder.sol (L145-148)
```text
    function balanceOf(address _account) public override view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L308-324)
```text
        );
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;

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

**File:** rewards/vlMGPBaseRewarder.sol (L386-390)
```text
    function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
        uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
        uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
        if (rewardableAmount > _amount)
            revert InvalidRewardableAmount();
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

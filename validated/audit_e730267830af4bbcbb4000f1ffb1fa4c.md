### Title
Instant reward-per-token accounting allows front-run "flash locking" to snipe forfeited MGP rewards - ([File: rewards/vlMGPBaseRewarder.sol])

### Summary
`_queueNewRewardsWithoutTransfer` (and `queueNewRewards`/`queueMGP`) update `rewardPerTokenStored` instantly and in full based on the *current* `totalStaked()` (i.e., `vlMGP.totalSupply()`) at the moment a forfeiture or reward is queued, with no time-weighted streaming (no `rewardRate`/`periodFinish` as in classic Synthetix-style pools). Because a new locker's reward checkpoint (`userRewardPerTokenPaid`) is only set to the pre-forfeiture `rewardPerTokenStored` value when they lock, and their `balanceOf()` is bumped to the full locked amount immediately, an attacker can lock a large amount of MGP in the same block right before a forfeiture event and claim a share of that forfeited reward proportional to their full stake, despite holding it for effectively zero time.

### Finding Description
`vlMGPBaseRewarder.totalStaked()` returns `vlMGP.totalSupply()` [1](#0-0) , and `balanceOf(_account)` reads the account's current staked amount straight from `MasterMagpie.stakingInfo` [2](#0-1) , both of which are live values with no vesting/snapshot delay for the reward-accounting purpose.

When a locker's reward is partially forfeited (via `_sendReward`, called from `getReward`/`getRewards`, or via `queueMGP`), the forfeited amount is folded into the pool through `_queueNewRewardsWithoutTransfer`, which increases `rewardPerTokenStored` by `forfeitAmount * 10**decimals / totalStaked()` using the `totalStaked()` value evaluated at that exact moment [3](#0-2)  and [4](#0-3) .

A user's claimable reward is `balanceOf(account) * (rewardPerTokenStored - userRewardPerTokenPaid[account]) / decimals + userRewards[account]` [5](#0-4) . The checkpoint `userRewardPerTokenPaid[account]` is only updated when the account's `updateRewards`/`updateReward` modifier or `_updateFor` runs [6](#0-5) [7](#0-6) .

Exploit flow:
1. Attacker sees a pending transaction that will trigger a forfeiture (e.g., another locker calling `getReward()`/`getRewards()` which computes `_calExpireForfeit` and forfeits unvested reward, or `MasterMagpie` routing MGP through `queueMGP`) [8](#0-7) .
2. Attacker front-runs with `VLMGP.lock(largeAmount)`, which increases `vlMGP.totalSupply()` and, through `MasterMagpie.depositVlMGPFor` → `_deposit`, triggers `_harvestBaseRewarder` → `rewarder.updateFor(account)` on the attacker using the pre-lock balance, checkpointing `userRewardPerTokenPaid[attacker]` to the pre-forfeiture `rewardPerTokenStored` [9](#0-8) [10](#0-9) . The attacker's `balanceOf()` in `vlMGPBaseRewarder` is now the full new locked amount.
3. Victim's transaction executes next in the same block, forfeiting reward and calling `_queueNewRewardsWithoutTransfer`, which bumps `rewardPerTokenStored` using `totalStaked()` that already includes the attacker's newly locked amount.
4. Attacker calls `getReward()`; because their checkpoint is stale (pre-bump) and their balance is now large, `_earned` credits them a share of the just-added reward proportional to their full new balance, even though they contributed zero duration to the pool prior to the event.

Existing protections do not stop this: `nonReentrant` and `onlyMasterMagpie`/`onlyManager` modifiers only guard against reentrancy/unauthorized callers, not against same-block stake-then-harvest sequencing; there is no minimum holding period, no `rewardRate`-based linear vesting of newly queued rewards, and no snapshot of `totalStaked()`/`balanceOf()` from before the triggering transaction.

### Impact Explanation
This results in theft of unclaimed/forfeited yield: an attacker with no prior economic contribution to the pool captures a portion of the forfeiture that should accrue only to lockers who held their position during the relevant period, diluting the honest, long-term stakers' share of the redistributed reward. This maps to the "theft of unclaimed yield" impact class. The magnitude of theft scales with the size of the attacker's temporary lock relative to `totalStaked()` and the size of the forfeiture event; it is capped by however large a forfeiture/reward-queue event is at that time, and requires the attacker to have enough capital to temporarily dominate `totalStaked()`.

### Likelihood Explanation
- No privileged role required: `VLMGP.lock()` is `external` and callable by anyone holding MGP [11](#0-10) ; `getReward`/`getRewards` on the rewarder are only gated by `onlyMasterMagpie`, but the *victim* (any normal user) triggers them, and the attacker only needs to front-run with their own `lock()` call and then call `getReward()`/`getRewards()` for themselves.
- Requires mempool visibility of a pending forfeiture-triggering transaction and the capital to lock a large amount of MGP for at least the duration needed to realize the reward (their principal remains locked/must go through `startUnlock`+cooldown to fully exit, but the reward itself is claimable immediately after the sandwich).
- Repeatable any time a sizeable forfeiture or reward top-up is about to be queued, which is a normal recurring event given `_calExpireForfeit` runs on virtually every unvested `getReward`/`queueMGP` call [12](#0-11) .
- Feasibility depends on real-world MEV/mempool conditions (private mempools, exact same-block ordering) but is a standard front-running pattern with no economic security beyond needing temporary capital.

### Recommendation
Introduce time-weighted distribution of queued/forfeited rewards instead of instantaneous full crediting to `rewardPerTokenStored`, e.g., stream newly queued rewards linearly over a fixed duration (similar to `rewardRate`/`periodFinish` patterns), or require a minimum lock/holding duration before a balance counts toward `totalStaked()`/`balanceOf()` for reward-accrual purposes. At minimum, snapshot `totalStaked()` and eligible balances prior to the transaction that triggers the forfeiture so that same-block stake additions cannot participate in that specific distribution.

### Proof of Concept
Foundry test plan:
1. Deploy `VLMGP`, `MasterMagpie`, `vlMGPBaseRewarder`, and MGP token with two lockers: `victim` (locks `X` MGP long before, with unvested rewards subject to forfeiture) and `attacker` (holds `largeAmount` MGP, unused initially).
2. Advance time so `victim` has partial-vesting `getRewardablePercentWAD` such that a `getReward()`/`queueMGP` call will forfeit a non-trivial amount.
3. In a single block (via `vm.startPrank`/manual ordering or a Foundry multicall simulating a bundle):
   a. `attacker` calls `vlMGP.lock(largeAmount)`.
   b. Trigger `victim`'s reward claim/queueMGP flow that calls `_sendReward` → `_queueNewRewardsWithoutTransfer(forfeitAmount, token)`.
   c. `attacker` calls `getReward()` on the rewarder.
4. Assert: `attacker`'s claimed reward for `token`/MGP after step (c) is > 0 and proportional to `largeAmount / vlMGP.totalSupply()` at the time of step (b), despite `attacker` having zero prior balance/duration in the pool.
5. Control test: repeat without the attacker's front-run lock (i.e., attacker locks well before or well after the forfeiture) and show the attacker's realized reward share is zero or negligible, confirming the front-run is what produces the disproportionate payout.

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

**File:** rewards/vlMGPBaseRewarder.sol (L134-139)
```text
    /// @notice Returns total current lock weighting, lock weighting is calculated by 
    /// amount of MGP still in lock + amount of MGP in cool down / 2
    /// @return Returns current amount of staked tokens
    function totalStaked() public override view returns (uint256) {
        return IERC20(address(vlMGP)).totalSupply();
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L141-148)
```text
    /// @notice Returns lock weighting of an user. Lock weighting is calculated by 
    /// amount of MGP still in lock + amount of MGP in cool down / 2
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(masterMagpie).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L274-289)
```text
    function queueMGP(uint256 _amount, address _account, address _receiver) override external onlyManager nonReentrant returns (bool) {
        IERC20(vlMGP.MGP()).safeTransferFrom(msg.sender, address(this), _amount);
        
        uint256 forfeitAmount = _calExpireForfeit(_account, _amount);
        uint256 rewardableAmount = _amount - forfeitAmount;
        
        if (forfeitAmount > 0)
            _queueNewRewardsWithoutTransfer(forfeitAmount, address(vlMGP.MGP()));

        if (rewardableAmount > 0) {
            IERC20(vlMGP.MGP()).safeTransfer(_receiver, rewardableAmount);
            emit MGPHarvested(_account, rewardableAmount, forfeitAmount);
        }

        return true;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L331-347)
```text
    function _queueNewRewardsWithoutTransfer(uint256 _amountReward, address _rewardToken) internal
    {
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards = rewardInfo.historicalRewards + _amountReward;
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
        emit ForfeitRewardAdded(_amountReward, _rewardToken);
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

**File:** rewards/vlMGPBaseRewarder.sol (L363-377)
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

**File:** rewards/vlMGPBaseRewarder.sol (L379-384)
```text
    function _earned(address _account, address _rewardToken, uint256 _userVlmgpShare) internal view returns (uint256) {
        return ((_userVlmgpShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**vlMGPDecimal) + userRewards[_rewardToken][_account];
    }
```

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

**File:** rewards/MasterMagpie.sol (L482-498)
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
```

**File:** rewards/MasterMagpie.sol (L631-636)
```text
    /// only update the reward counting on in base rewarder but not sending them to user
    function _harvestBaseRewarder(address _stakingToken, address _account) internal {
        IBaseRewardPool rewarder = IBaseRewardPool(tokenToPoolInfo[_stakingToken].rewarder);
        if (address(rewarder) != address(0))
            rewarder.updateFor(_account);
    }
```

**File:** VLMGP.sol (L252-258)
```text
    // @notice lock MGP in the contract
    // @param _amount the amount of MGP to lock
    function lock(uint256 _amount) override external whenNotPaused nonReentrant {
        _lock(msg.sender, msg.sender, _amount);

        emit NewLock(msg.sender, block.timestamp, _amount);
    }
```

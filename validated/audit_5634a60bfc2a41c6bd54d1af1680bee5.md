### Title
Front-runnable, non-time-weighted bonus reward distribution in `BaseRewardPool`/`BaseRewardPoolV2` allows just-in-time deposit/withdraw theft of unclaimed bonus yield - ([File: rewards/BaseRewardPool.sol, rewards/BaseRewardPoolV2.sol, rewards/MasterMagpie.sol])

### Summary
`MasterMagpie` tracks MGP emissions in a time-weighted manner (`accMGPPerShare` accrues only as `block.timestamp - lastRewardTimestamp` elapses), but the bonus-token rewarder (`BaseRewardPool`/`BaseRewardPoolV2`) distributes `queueNewRewards`/`donateRewards` amounts as an instantaneous lump-sum split among whoever is currently staked, with no time-weighting and no deposit/withdraw cooldown. An unprivileged attacker can front-run a reward-funding transaction with a large `deposit`, then immediately `withdraw`/claim right after, capturing bonus-token yield entirely disproportionate to their real staking duration, at the expense of genuine long-term stakers.

### Finding Description
`_provisionReward` in `rewards/BaseRewardPoolV2.sol` (and the analogous function in `BaseRewardPool.sol`) updates the global index as:
`rewardInfo.rewardPerTokenStored += (_amountReward * 10**decimals) / totalStaked()` [1](#0-0) 

This is evaluated once, atomically, at the moment `queueNewRewards`/`donateRewards` is called, using whatever `totalStaked()` (i.e. `IERC20(stakingToken).balanceOf(operator)`) happens to be at that instant, rather than accruing continuously per second like the MGP side. `earned()`/`_earned()` then simply multiply the user's current `balanceOf(_account)` by the delta between the current global index and the user's last-paid snapshot: [2](#0-1) 

`balanceOf(_account)` is a live read of `MasterMagpie.stakingInfo`, not a time-integrated value. [3](#0-2) 

Meanwhile, `MasterMagpie.updatePool` only accrues MGP proportional to elapsed time, so flash staking around it yields ~0 MGP: [4](#0-3) 

`_deposit`/`_withdraw` have no lockup, no cooldown, and no minimum holding period — they only call `updatePool` and `rewarder.updateFor(_account)` (which snapshots `userRewardPerTokenPaid` at the pre/post balance) before mutating `user.amount`: [5](#0-4) 

Exploit flow:
1. Attacker monitors the mempool for a `queueNewRewards` call (or `donateRewards`, which is permissionless) that will inject a bonus-token reward.
2. Attacker front-runs it with a large `deposit(_stakingToken, hugeAmount)` (optionally via a flash loan of the staking token, since there is no holding-time requirement).
3. The reward-funding transaction executes, bumping `rewardPerTokenStored` based on `totalStaked()` that now includes the attacker's large, freshly-added balance.
4. Attacker immediately calls `withdraw`/`multiclaimSpec`, which calls `rewarder.updateFor`/`getReward`, crediting `earned = balanceOf(attacker) * (newIndex - oldIndex)` — capturing a share of the bonus reward proportional to their capital, not their staking duration.
5. Attacker withdraws principal, having paid no time cost, diluting the yield available to real long-term stakers.

This does not corrupt the MGP-side accounting (which is correctly time-weighted and immune to this because reward requires elapsed `block.timestamp`), but it does let an unprivileged actor extract bonus-token yield they did not economically earn, at the expense of other stakers — a direct theft of unclaimed yield enabled by the desync between the two accrual models described in the question.

### Impact Explanation
This is a theft of unclaimed yield (bonus reward tokens) from legitimate long-term stakers, redirected to a just-in-time depositor who holds no real economic exposure to the pool. Matches Immunefi's "theft of unclaimed yield" impact class. The MGP-side accounting is not directly stolen from (it's time-weighted and self-protecting), but the bonus-rewarder side is fully exposed since it has no time-weighting or anti-JIT protection.

### Likelihood Explanation
- Requires only capital sufficient to temporarily dominate `totalStaked()` for one or two blocks; achievable via flash loan if the staking token (e.g. an LP/receipt token) is flash-loanable, or via capital the attacker already holds.
- `deposit`/`withdraw` are `external`, unprivileged, `whenNotPaused`/`nonReentrant` but with no cooldown or minimum stake duration, so nothing structurally prevents deposit-then-immediate-withdraw across two transactions.
- `donateRewards` is fully permissionless, so an attacker could even self-fund a reward round if profitable against other stakers' larger existing balances, though the more realistic vector is front-running legitimate `queueNewRewards` calls from the reward manager.
- Fully repeatable every time a bonus-reward funding transaction is broadcast.

### Recommendation
Convert the bonus-token reward distribution to a time-weighted streaming model (rewardRate over a duration, akin to Synthetix `StakingRewards`) instead of an instantaneous lump-sum split by current `totalStaked()`, or introduce a minimum staking duration / withdrawal cooldown before a user's balance is eligible to receive newly queued rewards, so that JIT deposits cannot capture rewards they did not economically wait for.

### Proof of Concept
Foundry test plan:
1. Deploy `MasterMagpie`, a pool with `BaseRewardPoolV2` bonus rewarder, and a bonus reward token; register a reward manager.
2. Have `UserA` deposit `100e18` staking tokens and wait a long period (so they've accrued real exposure).
3. Simulate mempool visibility: have `Attacker` call `deposit(stakingToken, 10_000e18)` (huge relative amount) immediately before the reward manager's `queueNewRewards(rewardAmount, bonusToken)` transaction executes.
4. Execute `queueNewRewards` in the next transaction (same block or next block, no delay).
5. Have `Attacker` immediately call `withdraw(stakingToken, 10_000e18)` and claim bonus rewards via `multiclaimSpec`.
6. Assert: `Attacker`'s claimed bonus reward ≈ `rewardAmount * 10_000e18/(10_000e18+100e18)` despite holding for effectively zero time, while `UserA`'s bonus share is diluted despite having staked for the entire reward period — demonstrating yield captured disproportionate to staking duration, in contrast to `Attacker`'s MGP `_calNewMGP` payout, which remains ~0 for the same window because MGP accrual is time-weighted via `pool.lastRewardTimestamp`.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L296-313)
```text
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
                (_amountReward * 10**stakingTokenDecimals) /
                totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
```

**File:** rewards/BaseRewardPool.sol (L130-136)
```text
    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/BaseRewardPool.sol (L173-185)
```text
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

**File:** rewards/MasterMagpie.sol (L374-396)
```text
    function updatePool(address _stakingToken) public whenNotPaused {
        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        if (block.timestamp <= pool.lastRewardTimestamp || totalAllocPoint == 0) {
            return;
        }
        uint256 lpSupply = _calLpSupply(_stakingToken);
        if (lpSupply == 0) {
            pool.lastRewardTimestamp = block.timestamp;
            return;
        }        
        uint256 multiplier = block.timestamp - pool.lastRewardTimestamp;
        uint256 mgpReward = (multiplier * mgpPerSec * pool.allocPoint) / totalAllocPoint;
        
        pool.accMGPPerShare = pool.accMGPPerShare + ((mgpReward * 1e12) / lpSupply);
        pool.lastRewardTimestamp = block.timestamp;

        emit UpdatePool(
            _stakingToken,
            pool.lastRewardTimestamp,
            lpSupply,
            pool.accMGPPerShare
        );
    }    
```

**File:** rewards/MasterMagpie.sol (L482-534)
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

    /// @notice internal function to deal with withdraw staking token
    function _withdraw(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        _harvestAndUnstake(_stakingToken, _account, _amount, _isVlMgp);

        if (!_isVlMgp)
            IERC20(tokenToPoolInfo[_stakingToken].stakingToken).safeTransfer(address(msg.sender), _amount);
        emit Withdraw(_account, _stakingToken, _amount);
    }

    function _harvestAndUnstake(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        updatePool(_stakingToken);

        UserInfo storage user = userInfo[_stakingToken][_account];

        if (!_isVlMgp && user.available < _amount)
            revert WithdrawAmountExceedsStaked();
        else if(user.amount < _amount && _isVlMgp)
            revert UnlockAmountExceedsLocked();
        
        _harvestMGP(_stakingToken, _account);
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount - _amount;
        
        if(!_isVlMgp)
            user.available = user.available - _amount;
        user.rewardDebt = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) / 1e12;
    }
```

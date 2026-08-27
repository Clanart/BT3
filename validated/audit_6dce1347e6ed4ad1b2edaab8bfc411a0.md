The analog exists: this codebase reproduces the identical unbounded-instant-reward-surge pattern from the `BkdLocker` report inside `BaseRewardPool` / `BaseRewardPoolV2` / `mWOMSVBaseRewarder`, combined with `MasterMagpie`'s deposit/withdraw functions which have **no** lock or withdrawal delay for regular staking pools (unlike `BkdLocker`, which at least has a configurable `_WITHDRAW_DELAY`).

### Title
Instant reward-surge in `BaseRewardPool`/`BaseRewardPoolV2` reward-per-share accounting can be front-run via `MasterMagpie` deposit/withdraw with zero lock-up delay - (File: `rewards/BaseRewardPool.sol`, `rewards/BaseRewardPoolV2.sol`, `rewards/MasterMagpie.sol`)

### Summary
`BaseRewardPool._provisionReward` and `BaseRewardPoolV2._provisionReward` immediately bump `rewardPerTokenStored` for the *entire* current `totalStaked()` supply whenever new rewards are added via `queueNewRewards`/`donateRewards`. Any account holding a share of `totalStaked()` at that instant is retroactively entitled to a slice of the new rewards, even if it staked one block earlier. Because `MasterMagpie.deposit`/`withdraw` (which drive `totalStaked()` for these reward pools) have no cooldown or delay at all, an attacker can deposit right before a reward-provisioning transaction, claim the pro-rata reward, and withdraw immediately — a more practical version of the `BkdLocker` "withdraw delay set to 0" scenario described in the report.

### Finding Description
`_provisionReward` computes: [1](#0-0) 
which divides the newly deposited `_amountReward` by `this.totalStaked()` taken at call time and adds it to `rewardPerTokenStored`. Every currently-staked token — regardless of how recently it was staked — immediately accrues a share of the reward via `rewardPerToken()` used in `earned()`/`_earned()`.

`BaseRewardPoolV2` and `mWOMSVBaseRewarder` implement the exact same pattern: [2](#0-1) [3](#0-2) 

Crucially, the staking token supply these reward pools track (`totalStaked()`) is driven by `MasterMagpie.deposit`/`withdraw`, which perform an unconditional, un-delayed transfer: [4](#0-3) 
and the internal `_deposit`/`_withdraw` logic contains no lock/cooldown check for ordinary LP-staking pools: [5](#0-4) 

This is functionally identical to the `BkdLocker#depositFees()` bug in the report: a reward surge is applied to the whole pool at once, and there's no mechanism (vesting, minimum holding period) preventing a staker from grabbing a share of the surge and immediately exiting. Unlike `BkdLocker`, where the admin at least has a `_WITHDRAW_DELAY` knob, `MasterMagpie`'s regular LP pools have *no* delay whatsoever, making the front-run trivially executable by any unprivileged wallet in a single block (or two consecutive blocks) via `deposit()` → wait for the reward-provisioning tx → `withdraw()`.

### Impact Explanation
An attacker can capture a disproportionate share of freshly-added `rewardToken`/bonus-token rewards intended for long-term stakers, directly reducing (stealing) the yield legitimately owed to existing depositors in the affected `BaseRewardPool`/`BaseRewardPoolV2`/`mWOMSVBaseRewarder` reward pools. This is a direct theft of unclaimed yield from other unprivileged users.

### Likelihood Explanation
Likelihood is high in this codebase relative to the original report because:
- `queueNewRewards`/`donateRewards` calls are visible in the mempool and can be sandwiched.
- `MasterMagpie.deposit`/`withdraw` for LP-staking pools have **no** lock-up or delay (unlike `BkdLocker`'s configurable delay), so the attacker needs only a deposit immediately before, and a withdraw immediately after, the reward-provisioning transaction — feasible within one or two blocks with no need for the admin to misconfigure anything.
- `donateRewards` itself is fully permissionless, meaning reward-provisioning events (which trigger the surge) are frequent and predictable for any registered reward token.

### Recommendation
Switch to a linear/gradual reward-release model (rate-based accrual over time, e.g., Synthetix's `StakingRewards`) instead of crediting the entire new reward amount to the current `totalStaked()` instantaneously. Alternatively, introduce a minimum staking duration or a per-deposit checkpoint that excludes very recently deposited balances from immediately-added rewards.

### Proof of Concept
1. Pool has `totalStaked() = 100,000` staking tokens, all held by long-term stakers.
2. Attacker observes a pending `queueNewRewards(1,000, rewardToken)` (or `donateRewards`) transaction in the mempool.
3. Attacker front-runs it with `MasterMagpie.deposit(stakingToken, 100,000)`, doubling `totalStaked()` to `200,000` in the same or preceding block.
4. The reward-provisioning transaction executes: `rewardPerTokenStored += 1,000 / 200,000`, crediting the attacker's newly deposited 100,000 tokens with 50% of the new rewards via `_provisionReward` [1](#0-0) .
5. Attacker calls `getReward`/claim through `MasterMagpie` to collect the accrued `rewardToken`, then immediately calls `MasterMagpie.withdraw(stakingToken, 100,000)` [6](#0-5)  — with no lock-up delay, exiting the position entirely.
6. Result: the attacker has stolen roughly 500 `rewardToken` that should have accrued to the pre-existing stakers, at essentially zero cost/risk beyond gas and momentary capital lockup.

### Citations

**File:** rewards/BaseRewardPool.sol (L297-318)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
        );
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;
        if (this.totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**stakingDecimals()) /
                this.totalStaked();
        }
```

**File:** rewards/BaseRewardPoolV2.sol (L290-313)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
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
                (_amountReward * 10**stakingTokenDecimals) /
                totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
```

**File:** rewards/mWOMSVBaseRewarder.sol (L305-327)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20Metadata(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
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
                (_amountReward * 10**mWOMSVDecimal) / totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
```

**File:** rewards/MasterMagpie.sol (L337-346)
```text
    function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _deposit(_stakingToken, msg.sender, _amount, false);
    }

    /// @notice Withdraw staking tokens from Master Mgapie.
    /// @param _stakingToken Staking token of the pool
    /// @param _amount amount to withdraw
    function withdraw(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _withdraw(_stakingToken, msg.sender, _amount, false);
    }
```

**File:** rewards/MasterMagpie.sol (L482-514)
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
```

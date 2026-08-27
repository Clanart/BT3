### Title
Flashloan-based reward sniping via permissionless `WombatStaking.harvest()` and instant `rewardPerTokenStored` update in `BaseRewardPool` - ([File: wombat/WombatStaking.sol], [File: rewards/BaseRewardPool.sol])

### Summary
`BaseRewardPool` credits rewards instantly to whatever `totalStaked()` is at the moment `queueNewRewards`/`donateRewards` is called, with no time-weighted streaming or minimum holding period. Because `WombatStaking.harvest()` is fully permissionless and callable atomically, and because deposits into a pool immediately count toward `totalStaked()` before any reward is queued, an attacker can flashloan-deposit into a pool, force-harvest the accrued WOM/bonus rewards, and immediately withdraw — capturing a share of rewards that legitimately accrued to long-term stakers, all within a single transaction.

### Finding Description
`BaseRewardPool._provisionReward` distributes newly queued rewards proportionally to the *current* `totalStaked()`: [1](#0-0) 

`totalStaked()` reads the live balance of the receipt/staking token held by `MasterMagpie`, and `balanceOf(account)` reads the user's live staked amount: [2](#0-1) 

When a user deposits into `MasterMagpie`, their `userRewardPerTokenPaid` checkpoint (`rewarder.updateFor`) is taken *before* their new stake is added to the pool (`_harvestBaseRewarder` runs before `user.amount` is incremented), so the deposit itself never lets the depositor immediately dilute the reward they capture: [3](#0-2) 

However, `WombatStaking.harvest()` is a separate, fully unprivileged, permissionless entry point that anyone can call at any time to force a reward pull from Wombat and push it into `BaseRewardPool` via `queueNewRewards`: [4](#0-3) [5](#0-4) 

This means an attacker can, in one atomic transaction:
1. Flashloan the pool's deposit token and call `WombatPoolHelper.deposit()`, which mints receipt tokens and stakes them into `MasterMagpie` on the attacker's behalf, setting the attacker's `userRewardPerTokenPaid` checkpoint to the pre-harvest value (`_deposit` in `MasterMagpie.sol:481-505`, `WombatPoolHelper._deposit`/`_stake` in `wombat/WombatPoolHelper.sol:148-165`).
2. Call `WombatStaking.harvest(_lpToken)` directly, which pulls accrued WOM (and bonus) rewards that accumulated over real elapsed time for all prior stakers, and calls `queueNewRewards` — now `totalStaked()` in `BaseRewardPool` already includes the attacker's freshly staked (flashloaned) balance, so `rewardPerTokenStored` jumps by an amount split across a supply artificially inflated by the attacker.
3. Immediately call `WombatPoolHelper.withdraw()`, which triggers `_harvestAndUnstake`/`getReward` in `MasterMagpie`/`BaseRewardPool`, computing `earned()` using the attacker's large `balanceOf` against the just-updated `rewardPerToken`, paying out a disproportionate share of the reward to the attacker.
4. Unwind the position and repay the flashloan.

Because reward accrual has no vesting/streaming and no lock/cooldown period tying reward eligibility to actual holding duration, and `harvest()` has zero access control, the entire sequence is reachable purely from an ordinary wallet.

### Impact Explanation
This results in direct theft of unclaimed yield belonging to legitimate long-term stakers of a given Wombat pool (WOM emissions and any bonus/bribe reward tokens routed through `BaseRewardPool.queueNewRewards`). The stolen value scales with the size of the flashloaned deposit relative to the pool's existing `totalStaked()` and the amount of reward pending harvest, and is permanently extracted from the reward pool at the expense of honest depositors.

### Likelihood Explanation
Likelihood is high: `WombatStaking.harvest()` has no access control beyond `_onlyActivePool`, flashloans for the relevant deposit tokens (stablecoins/LPs on the target chain) are readily available, and the entire attack executes atomically within a single transaction with no need for privileged access, governance, or oracle manipulation.

### Recommendation
Decouple reward eligibility from instantaneous stake by either (a) streaming/vesting newly queued rewards over time instead of crediting them instantly to `rewardPerTokenStored`, (b) enforcing a minimum staking duration or checkpointing rewards based on a time-weighted average balance, or (c) disallowing same-block deposit+harvest+withdraw sequences (e.g., requiring at least one block between stake changes and reward-affecting harvests, or snapshotting `totalStaked()`/user balances prior to the block in which `harvest()` executes).

### Proof of Concept
1. Attacker takes a flashloan of the pool's `depositToken`.
2. Attacker calls `WombatPoolHelper.deposit()` (`wombat/WombatPoolHelper.sol:148-155`), receiving receipt tokens staked into `MasterMagpie` via `depositFor` (`rewards/MasterMagpie.sol:481-505`), with `userRewardPerTokenPaid` checkpointed at the pre-harvest `rewardPerTokenStored`.
3. Attacker calls `WombatStaking.harvest(lpToken)` (`wombat/WombatStaking.sol:331-335`), which pulls pending WOM/bonus rewards accrued from real elapsed time and calls `BaseRewardPool.queueNewRewards` (`rewards/BaseRewardPool.sol:261-274, 297-320`), inflating `rewardPerTokenStored` based on a `totalStaked()` that now includes the attacker's flashloaned stake.
4. Attacker calls `WombatPoolHelper.withdraw()` (`wombat/WombatPoolHelper.sol:121-140`), triggering `_harvestAndUnstake`/`getReward`, which pays out `earned()` computed against the attacker's large temporary balance and the newly inflated `rewardPerToken`.
5. Attacker repays the flashloan, keeping the disproportionate reward share extracted from the pool.

### Citations

**File:** rewards/BaseRewardPool.sol (L124-136)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }

    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/BaseRewardPool.sol (L297-319)
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
        emit RewardAdded(_amountReward, _rewardToken);
```

**File:** rewards/MasterMagpie.sol (L481-505)
```text
    /// @notice internal function to deal with deposit staking token
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

**File:** wombat/WombatStaking.sol (L329-335)
```text
    /// @notice harvest a Pool from Wombat
    /// @param _lpToken wombat pool lp as helper identifier
    function harvest(
        address _lpToken
    ) whenNotPaused _onlyActivePool(_lpToken) external {
        _toMasterWomAndSendReward(_lpToken, 0, true); // triggers harvest from wombat exchange
    }
```

**File:** wombat/WombatStaking.sol (L671-696)
```text
    function _toMasterWomAndSendReward(address _lpToken, uint256 lpAmount, bool _isStake) internal {
        Pool storage poolInfo = pools[_lpToken];

        address[] memory bonusTokens = assetToBonusRewards[_lpToken];
        uint256 bonusTokensLength = bonusTokens.length;

        uint256 womBeforeBalance = IERC20(wom).balanceOf(address(this));
        uint256[] memory beforeBalances = _rewardBeforeBalances(_lpToken);

        if(_isStake)
            _stakeToWombatMaster(_lpToken, lpAmount); // triggers harvest from wombat exchange
        else
            IMasterWombat(masterWombat).withdraw(poolInfo.pid, lpAmount); // triggers harvest from wombat exchange
        uint256 womRewards = IERC20(wom).balanceOf(address(this)) - womBeforeBalance;
        _sendRewards(_lpToken, wom, poolInfo.rewarder, womRewards);

        for (uint256 i; i < bonusTokensLength; i++) {
            uint256 bonusBalanceDiff = IERC20(bonusTokens[i]).balanceOf(address(this)) - beforeBalances[i];
            if (bonusBalanceDiff > 0) {
                _sendRewards(_lpToken, bonusTokens[i], poolInfo.rewarder, bonusBalanceDiff);
            }
        }

        emit WomHarvested(womRewards);

    }
```

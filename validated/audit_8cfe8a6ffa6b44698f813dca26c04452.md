### Title
Reward-per-token lump-sum harvest allows sniping/stealing of pending WOM yield from existing stakers - (File: `rewards/BaseRewardPool.sol`, `wombat/WombatStaking.sol`)

### Summary
`BaseRewardPool` (and its variants `BaseRewardPoolV2.sol`, `mWOMSVBaseRewarder.sol`) distribute reward tokens using an instantaneous, lump-sum `rewardPerTokenStored` bump whenever `queueNewRewards`/`_provisionReward` is called, rather than streaming rewards continuously over time. Because `WombatStaking.harvest()` and the `deposit()`/`withdraw()` flows in `WombatStaking.sol` are permissionless and pull real, previously-accrued WOM emissions from `MasterWombat` in one shot via `_toMasterWomAndSendReward`, an attacker can deposit a large stake immediately before triggering (or front-running) a harvest, and withdraw immediately after, capturing a disproportionate share of yield that legitimately accrued to long-term stakers.

### Finding Description
`BaseRewardPool._provisionReward` (called from `queueNewRewards`) updates the global accumulator in a single step, proportional to `totalStaked()` at the moment of the call: [1](#0-0) 

`rewardPerToken()` simply returns this stored value with no time-weighting: [2](#0-1) 

and `earned()` is computed purely from the current balance and the delta of this accumulator since the user's last checkpoint: [3](#0-2) 

`totalStaked()` reads live `MasterMagpie` balance of the staking (receipt) token: [4](#0-3) 

The lump-sum injection is triggered from `WombatStaking._toMasterWomAndSendReward`, called on every `deposit`, `withdraw`, and the permissionless `harvest()`: [5](#0-4) [6](#0-5) 

`MasterMagpie._deposit` sets the depositor's `userRewardPerTokenPaid` checkpoint (via `_harvestBaseRewarder`) *before* increasing `user.amount`, i.e., before the depositor's new stake counts toward `totalStaked()`: [7](#0-6) 

Because the WOM emissions pulled from `MasterWombat` had accrued (based on the smaller, pre-attack `totalStaked()`) over the time since the last harvest, but are distributed pro-rata over the *post-deposit* (larger) `totalStaked()`, an attacker who deposits immediately before a harvest event and withdraws immediately after captures a slice of yield that was actually earned by the pre-existing stakers, diluting their rightful rewards. This is the same "sudden reward surge" sandwich class described in the external report (`BathToken`/`rebalancePair`), just manifesting through the reward-per-share accumulator instead of `pricePerShare`.

### Impact Explanation
This allows theft of unclaimed/pending WOM yield from legitimate long-term stakers by any ordinary wallet, without needing any privileged role — `harvest()` is permissionless (`WombatPoolHelper.harvest()` / `WombatStaking.harvest()`), and deposit/withdraw are open to all users. The attacker does not even need to front-run a third party's transaction; they can execute deposit → harvest → withdraw themselves back-to-back, capturing a share of WOM proportional to `attacker_stake / totalStaked` of rewards that accrued entirely before they staked.

### Likelihood Explanation
High. The attack requires no special conditions beyond the presence of unharvested pending WOM rewards in `MasterWombat` (which naturally accumulate between harvests) and sufficient capital to temporarily dominate `totalStaked()` for one block/transaction sequence. `harvest()` being callable by anyone removes the need to even wait for or front-run a strategist/keeper action.

### Recommendation
Do not apply newly harvested rewards to the current `totalStaked()` instantaneously. Instead, stream rewards linearly over a fixed duration (as in Synthetum-style `rewardRate`/`periodFinish` staking reward pools), or checkpoint/exclude balance changes that occur within the same block/harvest window from receiving a share of rewards accrued prior to their deposit. Alternatively, require a minimum staking duration before a deposit is eligible to receive a proportional share of freshly harvested rewards.

### Proof of Concept
1. Assume `WombatStaking`'s position in `MasterWombat` for a given `lpToken` has accrued, say, `1,000 WOM` in pending, unharvested rewards, and `totalStaked()` (receipt tokens staked in `MasterMagpie`) is currently `100,000`.
2. Attacker calls `WombatPoolHelper.deposit()` for `100,000` (doubling `totalStaked()` to `200,000`); this internally calls `MasterMagpie.depositFor`, which checkpoints the attacker's `userRewardPerTokenPaid` to the pre-harvest value [8](#0-7) .
3. Attacker (or anyone) calls `WombatPoolHelper.harvest()` → `WombatStaking.harvest()` → `_toMasterWomAndSendReward` pulls the `1,000 WOM` from `MasterWombat` and calls `queueNewRewards`, which bumps `rewardPerTokenStored` by `1,000 * 1e18 / 200,000` (using the now-diluted `totalStaked()`) [9](#0-8) .
4. Attacker immediately calls `WombatPoolHelper.withdraw()`; `MasterMagpie._harvestAndUnstake` computes `earned()` for the attacker using their large balance against the full `rewardPerToken` delta, paying them roughly `500 WOM` — half of the reward pool that had accrued entirely from the original stakers' historical positions [3](#0-2) .
5. The remaining original stakers are left with only `500 WOM` instead of the `1,000 WOM` they actually earned, permanently losing half their yield to the attacker.

### Citations

**File:** rewards/BaseRewardPool.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }
```

**File:** rewards/BaseRewardPool.sol (L141-148)
```text
    function rewardPerToken(address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return rewards[_rewardToken].rewardPerTokenStored;
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

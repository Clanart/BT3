### Title
Reward sandwiching via permissionless `harvest()` lets depositors steal a share of WOM rewards from long-term stakers with zero commitment - ([File: wombat/WombatStaking.sol], [File: rewards/BaseRewardPool.sol])

### Summary
`WombatStaking.harvest()` is a fully permissionless, unprivileged function that anyone can call at any time to trigger reward realization from Wombat and push it into the pool's `BaseRewardPool` via `queueNewRewards`. Because `BaseRewardPool`/`BaseRewardPoolV2` reward accounting instantly and irreversibly bumps `rewardPerTokenStored` for the *current* total staked balance, and because `MasterMagpie`/`WombatPoolHelper` deposits and withdrawals have no cooldown, vesting, or minimum-permanency period, an attacker can deposit LP, self-trigger `harvest()`, and withdraw immediately — capturing a proportional share of freshly harvested rewards with a holding period of essentially zero blocks. This mirrors the Votium/Convex "reward sandwiching" finding, but is worse here because the reward-realization trigger itself is public rather than requiring front-running an operator's claim transaction.

### Finding Description
`WombatStaking.harvest(_lpToken)` [1](#0-0)  has only an `_onlyActivePool` modifier — any wallet can call it. It calls `_toMasterWomAndSendReward(_lpToken, 0, true)` [2](#0-1) , which stakes `0` LP into `MasterWombat` (a call whose sole purpose per the inline comment is to "trigger harvest from wombat exchange"), measures the WOM balance delta received, and forwards it to the pool's `BaseRewardPool` through `_sendRewards` → `queueNewRewards` [3](#0-2) .

`BaseRewardPool._provisionReward` (and the equivalent in `BaseRewardPoolV2`) applies the new reward immediately to all currently staked balance by increasing `rewardPerTokenStored`: [4](#0-3) 

`earned()` computes a user's share purely from their current `balanceOf` and the `rewardPerToken` delta since their last checkpoint, with no vesting or time-weighting: [5](#0-4) 

Deposits and withdrawals through `WombatPoolHelper`/`WombatPoolHelperV2` and `MasterMagpie` are unrestricted and instantaneous — there is no minimum staking duration, cooldown, or delayed-withdrawal queue anywhere in `MasterMagpie._deposit`/`_withdraw` [6](#0-5)  or in `WombatPoolHelper.deposit`/`withdraw` [7](#0-6) .

Combining these facts: a user can (1) deposit LP into a Wombat pool via the pool helper, (2) immediately call the public `WombatStaking.harvest()` for that pool to force-realize pending WOM rewards into the `BaseRewardPool`, instantly bumping `rewardPerTokenStored` for the whole pool, and (3) immediately withdraw, during which `_harvestBaseRewarder`/`getReward` pays out `earned()` based on the just-updated `rewardPerTokenStored`. The attacker's balance is included in the reward distribution denominator/numerator despite having been staked for a negligible time, diluting the rewards that should have accrued to long-term stakers.

### Impact Explanation
This results in theft of unclaimed yield from legitimate, long-term Wombat/Magpie stakers: an attacker can extract a proportional share of every harvested WOM/bonus-token reward batch without bearing the economic exposure (LP risk, time value) that honest stakers assume. Because the attack does not require front-running (the reward trigger is a public function the attacker can call itself), it can be executed reliably and repeatedly against every harvest event, continuously siphoning yield away from committed depositors. This matches the "theft of unclaimed yield" impact category.

### Likelihood Explanation
Likelihood is high: `harvest()` requires no privileged role, no oracle manipulation, and no reliance on a third party's transaction ordering — the attacker can call it themselves in the same transaction flow as their deposit/withdraw. The only requirement is that the pool has accrued pending WOM/bonus rewards on Wombat, which is a normal, frequent, and externally observable condition (rewards accrue continuously in `MasterWombat`).

### Recommendation
Introduce a minimum staking/permanency delay before newly deposited balances become eligible for `getReward`/`earned()` payouts in `BaseRewardPool`/`BaseRewardPoolV2`, or stream newly queued rewards linearly over time instead of crediting them to `rewardPerTokenStored` in a single atomic step in `_provisionReward`. Alternatively, restrict `WombatStaking.harvest()` so it cannot be triggered arbitrarily by any user immediately after a deposit (e.g., rate-limit harvests or decouple deposit eligibility from the harvest timing).

### Proof of Concept
1. Attacker calls `WombatPoolHelper.deposit(amount, minLiquidity)` [8](#0-7) , which stakes receipt tokens into `MasterMagpie` on the attacker's behalf.
2. Attacker (or anyone) calls `WombatStaking.harvest(_lpToken)` [1](#0-0) , forcing realization of pending WOM rewards and pushing them into the pool's `BaseRewardPool` via `queueNewRewards`, which immediately raises `rewardPerTokenStored` [4](#0-3)  for the entire staked pool, including the attacker's just-deposited balance.
3. Attacker calls `WombatPoolHelper.withdraw(liquidity, minAmount)` [9](#0-8) , which triggers `MasterMagpie.withdrawFor` → `_harvestAndUnstake` → `_harvestBaseRewarder`, paying out `earned()` rewards computed from the newly bumped `rewardPerTokenStored` [5](#0-4) , despite the attacker having staked for only the duration of steps 1–3.
4. The attacker walks away with a share of the harvested rewards while the LP position was held for a negligible amount of time, at the expense of stakers who remained in the pool.

### Citations

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

**File:** wombat/WombatStaking.sol (L715-770)
```text
    /// @notice Send rewards to the rewarders
    /// @param _rewardToken the address of the reward token to send
    /// @param _rewarder the rewarder that will get the rewards
    /// @param _amount the initial amount of rewards after harvest

    function _sendRewards(
        address _lpToken,
        address _rewardToken,
        address _rewarder,
        uint256 _amount
    ) internal {
        if (_amount == 0) return;
        uint256 originalRewardAmount = _amount;

        if (!isPoolFeeFree[_lpToken]) {
            for (uint256 i = 0; i < feeInfos.length; i++) {
                Fees storage feeInfo = feeInfos[i];

                if (feeInfo.isActive) {
                    address rewardToken = _rewardToken;
                    uint256 feeAmount = (originalRewardAmount * feeInfo.value) / DENOMINATOR;
                    _amount -= feeAmount;
                    uint256 feeTosend = feeAmount;

                    if (feeInfo.isMWOM && rewardToken == wom) {
                        if (smartWomConverter != address(0)) {
                            IERC20(wom).safeApprove(smartWomConverter, feeAmount);
                            uint256 beforeBalnce = IMWom(mWom).balanceOf(address(this));
                            IConverter(smartWomConverter).smartConvert(feeAmount, 0);
                            rewardToken = mWom;
                            feeTosend = IMWom(mWom).balanceOf(address(this)) - beforeBalnce;
                        } else {
                            IERC20(wom).safeApprove(mWom, feeAmount);
                            uint256 beforeBalnce = IMWom(mWom).balanceOf(address(this));
                            IMWom(mWom).deposit(feeAmount);
                            rewardToken = mWom;
                            feeTosend = IMWom(mWom).balanceOf(address(this)) - beforeBalnce;
                        }
                    }

                    if (!feeInfo.isAddress) {
                        IERC20(rewardToken).safeApprove(feeInfo.to, 0);
                        IERC20(rewardToken).safeApprove(feeInfo.to, feeTosend);
                        IBaseRewardPool(feeInfo.to).queueNewRewards(feeTosend, rewardToken);
                    } else {
                        IERC20(rewardToken).safeTransfer(feeInfo.to, feeTosend);
                        emit RewardPaidTo(feeInfo.to, rewardToken, feeTosend);
                    }
                }
            }
        }

        IERC20(_rewardToken).safeApprove(_rewarder, 0);
        IERC20(_rewardToken).safeApprove(_rewarder, _amount);
        IBaseRewardPool(_rewarder).queueNewRewards(_amount, _rewardToken);
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

**File:** rewards/MasterMagpie.sol (L481-534)
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

**File:** wombat/WombatPoolHelper.sol (L96-140)
```text
    /// @notice deposit stables in wombat pool, autostake in master magpie    
    /// @param _amount the amount of stables to deposit
    function deposit(uint256 _amount, uint256 _minimumLiquidity) external override {
        _deposit(_amount, _minimumLiquidity, msg.sender);
    }

    function depositLP(uint256 _lpAmount) external {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).depositLP(lpToken, _lpAmount, msg.sender);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, msg.sender);
        
        emit NewLpDeposit(msg.sender, _lpAmount);
    }

    function depositNative(uint256 _minimumLiquidity) external payable {
        if(!isNative) revert NotNativeToken();
        // Dose need to limit the amount must > 0?

        // Swap the BNB to wBNB
        _wrapNative();
        // depsoit wBNB to the pool
        IWNative(depositToken).approve(wombatStaking, msg.value);
        _deposit(msg.value, _minimumLiquidity, address(this));
        IWNative(depositToken).approve(wombatStaking, 0);
    }

    /// @notice withdraw stables from wombat pool, auto unstake from master Magpie
    /// @param _liquidity the amount of liquidity to withdraw
    function withdraw(uint256 _liquidity, uint256 _minAmount) external override {
        // we have to withdraw from wombat exchange to harvest reward to base rewarder
        IWombatStaking(wombatStaking).withdraw(
            lpToken,
            _liquidity,
            _minAmount,
            msg.sender
        );
        // then we unstake from master wombat to trigger reward distribution from basereward
        _unstake(_liquidity, msg.sender);
        //  last burn the staking token withdrawn from Master Magpie
        IWombatStaking(wombatStaking).burnReceiptToken(lpToken, _liquidity);


        emit NewWithdraw(msg.sender, _liquidity);
    }
```

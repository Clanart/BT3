## Analysis Result

### Title
Reward-sniping via permissionless `harvest()` + instant deposit/withdraw lets an attacker steal freshly-harvested WOM/bonus yield from long-term LP stakers - (File: `wombat/WombatStaking.sol`, `rewards/BaseRewardPoolV2.sol`, `rewards/MasterMagpie.sol`)

### Summary
The original Yieldy finding exploits a reward-distribution model where the pool instantly credits pending rewards proportional to the caller's *current* balance, and lets an attacker bracket a reward-triggering event with a deposit and an immediate withdrawal to capture rewards with near-zero exposure time. MagpieXYZ's Wombat integration reproduces the same root cause: rewards are injected into `BaseRewardPoolV2` via `_provisionReward`, which immediately bumps `rewardPerTokenStored` in proportion to `totalStaked()` at that instant [1](#0-0) , and both the reward-triggering `harvest()` call and the deposit/withdraw path into `MasterMagpie` are fully permissionless with no warm-up/lock delay.

### Finding Description
`WombatStaking.harvest(_lpToken)` is a public, unprivileged function (`whenNotPaused _onlyActivePool`) that anyone can call to pull pending WOM/bonus rewards from Wombat Exchange and push them into the pool's `BaseRewardPoolV2` via `queueNewRewards` → `_provisionReward` [2](#0-1) [3](#0-2) .

`_provisionReward` distributes the harvested amount immediately across `totalStaked()`, which is simply the current balance staked in `MasterMagpie` at the moment `harvest()` executes: [4](#0-3) 

Deposits into `MasterMagpie` (via `WombatPoolHelper.deposit`/`depositFor`) and withdrawals (`withdrawFor`) have no warm-up period, cooldown, or minimum holding time — `_deposit`/`_withdraw` simply update `user.amount`/`user.available` and `rewardDebt` on the spot [5](#0-4) . `WombatPoolHelper.withdraw` immediately unstakes and burns the receipt token with no delay [6](#0-5) .

This mirrors the report's root cause exactly: an ordinary wallet can (1) deposit LP tokens right before a `harvest()` call (either front-running someone else's harvest transaction, or simply calling `harvest()` itself in the very next block), (2) have `_provisionReward` credit `rewardPerTokenStored` using a `totalStaked()` that now includes the attacker's freshly-added principal, entitling the attacker to a slice of rewards accrued by long-term LPs before the attacker ever staked, and (3) immediately call `withdraw`/`multiclaimFor` to collect the reward and exit, with essentially zero token downtime.

### Impact Explanation
This constitutes theft of unclaimed yield: the WOM/bonus-token rewards distributed by `_provisionReward` are meant to compensate LPs for the time they were staked, but the flat "credit based on current totalStaked" mechanic (no time-weighting, no warm-up) lets a flash-in/flash-out attacker dilute and redirect a share of that yield to themselves at the expense of genuine long-term stakers, exactly as in the referenced Yieldy finding.

### Likelihood Explanation
Every step is reachable from an ordinary, unprivileged wallet: `harvest()` is public and callable by anyone [2](#0-1) , `deposit`/`withdraw` on `MasterMagpie`/`WombatPoolHelper` are unprivileged and have no lock [7](#0-6) , and no admin or governance action is required. The only requirement is timing the deposit before a harvest event and withdrawing right after, both of which are trivially achievable via mempool observation or by self-triggering the harvest.

### Recommendation
Introduce a time-weighted reward accounting model (e.g., a minimum staking duration or a warm-up period before newly deposited principal becomes eligible for freshly queued rewards), or restrict `queueNewRewards`/`harvest` triggers so that reward accrual is based on time-integrated balances rather than instantaneous `totalStaked()` snapshots. This closes the same class of "flash stake to snipe reward distribution" issue identified in the referenced report.

### Proof of Concept
1. Attacker observes (or self-triggers) an imminent `WombatStaking.harvest(_lpToken)` call.
2. In the preceding block, attacker calls `WombatPoolHelper.deposit(...)`, which calls `MasterMagpie.depositFor` and stakes the receipt token with no lock [8](#0-7) .
3. `harvest()` executes, `_toMasterWomAndSendReward` → `_sendRewards` → `queueNewRewards` → `_provisionReward` distributes the harvested WOM proportional to `totalStaked()`, which now includes the attacker's deposit [9](#0-8) .
4. Attacker immediately calls `WombatPoolHelper.withdraw(...)` / `MasterMagpie.multiclaimFor`, collecting their share of the reward and removing their principal, with no warm-up penalty.

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

**File:** wombat/WombatStaking.sol (L331-335)
```text
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

**File:** wombat/WombatPoolHelper.sol (L123-140)
```text
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

**File:** wombat/WombatPoolHelper.sol (L148-155)
```text
    function _deposit(uint256 _amount, uint256 _minimumLiquidity, address _from) internal {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, msg.sender, _from);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, msg.sender);
        
        emit NewDeposit(msg.sender, _amount);
    }
```

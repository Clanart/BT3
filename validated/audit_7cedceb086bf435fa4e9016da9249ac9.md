### Title
Unpermissioned `WombatStaking.harvest()` allows flashloan-style front-running of `BaseRewardPool`/`BaseRewardPoolV2` reward accrual to steal freshly-harvested WOM/bonus rewards from real LP stakers - (`File: wombat/WombatStaking.sol`, `rewards/BaseRewardPool.sol`, `rewards/BaseRewardPoolV2.sol`)

### Summary
`WombatStaking.harvest()` is callable by any unprivileged wallet with no access control other than the pool being active, and it triggers an immediate reward distribution into `BaseRewardPool`/`BaseRewardPoolV2` proportional to `totalStaked()` measured at that exact instant. Because `BaseRewardPool.balanceOf()`/`totalStaked()` reflect the live `MasterMagpie` staking ledger rather than any time-weighted or vesting-based accounting, an attacker can deposit a large LP position immediately before calling `harvest()`, capture a disproportionate share of the newly queued rewards, and withdraw right after — diluting the rewards genuine long-term stakers should have earned.

### Finding Description
`WombatStaking.harvest(address _lpToken)` has only an `_onlyActivePool` check, no restriction on caller: [1](#0-0) 

It calls `_toMasterWomAndSendReward`, which harvests WOM (and bonus tokens) from Wombat and immediately forwards them via `_sendRewards` → `IBaseRewardPool(_rewarder).queueNewRewards(...)`: [2](#0-1) [3](#0-2) 

`queueNewRewards` → `_provisionReward` distributes the harvested amount by dividing it across `totalStaked()` **at the moment of the call**: [4](#0-3) 

`totalStaked()` and `balanceOf(account)` are read live from `MasterMagpie`'s staking ledger, not from any time-weighted checkpoint: [5](#0-4) 

`MasterMagpie._deposit`/`_withdraw` have no minimum holding period, cooldown, or same-block deposit/withdraw restriction — a user can deposit and withdraw in the same transaction: [6](#0-5) 

Combining these: an attacker can, in a single transaction (financed via flash-borrowed LP/deposit tokens or their own capital), (1) deposit a large stake into the pool through `WombatPoolHelperV2`/`MasterMagpie.depositFor`, (2) call `WombatStaking.harvest(_lpToken)` to force `queueNewRewards` to run while their inflated stake dominates `totalStaked()`, capturing a share of `rewardPerTokenStored` proportional to their temporary balance, then (3) withdraw immediately (`_harvestAndUnstake` pays out `userRewards` before decrementing `user.amount`), pocketing rewards that should have accrued to depositors who held stake for the actual harvest interval. This does not require compromising any privileged role — `harvest()` is explicitly public/unprivileged and deposit/withdraw are ordinary user flows.

### Impact Explanation
This is a direct theft of unclaimed yield from real WOM LP stakers: each time the attacker repeats the deposit→harvest→withdraw sandwich, a portion of the accrued `wom`/bonus reward stream that should be distributed pro-rata by time-in-pool is instead captured by a large temporary depositor who contributed no real time-weighted exposure. Because pool rewards accrue continuously from Wombat's `MasterWombat`, this can be repeated whenever new bribe/gauge or emission rewards land in `WombatStaking`, resulting in a persistent and repeatable value transfer to the attacker at the expense of long-term LP stakers/vlMGP participants — satisfying "theft of unclaimed yield."

### Likelihood Explanation
The attack requires only capital (which can be obtained via flashloan of the pool's deposit token or LP token, both of which are typically abundant in DEX pools), a single transaction, and calling three fully public functions (`deposit`/`depositFor`, `harvest`, `withdraw`/`withdrawFor`). No governance, oracle, or admin interaction is needed, making this reachable directly by any unprivileged wallet.

### Recommendation
- Restrict `WombatStaking.harvest()` to authorized keepers/managers, or add a minimum time-since-last-harvest / minimum time-staked gate before a depositor's balance counts toward `queueNewRewards` distribution.
- Alternatively, change `BaseRewardPool`/`BaseRewardPoolV2` reward accrual to a time-weighted/checkpointed model (e.g., snapshot balances prior to the harvest block, or accrue per-second like `MasterMagpie.accMGPPerShare` rather than instantaneous `totalStaked()`).
- Add a deposit-cooldown before newly deposited stake is eligible to receive freshly queued rewards from `queueNewRewards`.

### Proof of Concept
1. Attacker flashloans/acquires a large amount of the pool's `depositToken`.
2. Attacker calls `WombatPoolHelperV2.deposit(...)` → `WombatStaking.deposit(...)` → `MasterMagpie.depositFor(stakingToken, largeAmount, attacker)`, inflating `attacker`'s share of `totalStaked()` in the target `BaseRewardPoolV2`.
3. In the same transaction, attacker (or anyone) calls `WombatStaking.harvest(_lpToken)`, which harvests pending WOM from `MasterWombat` and calls `queueNewRewards`, computing `rewardPerTokenStored` using the now-inflated `totalStaked()` that includes the attacker's flash deposit — [7](#0-6) .
4. Attacker calls `MasterMagpie.withdrawFor`/`withdraw` → `_harvestAndUnstake`, which pays out `userRewards` computed from `earned()`/`_earned()` based on the attacker's large `balanceOf`, before removing the flash-deposited stake — [8](#0-7) .
5. Attacker repays the flashloan and keeps the disproportionate reward share, which is deducted from what remaining/long-term stakers would have received from that harvest.

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

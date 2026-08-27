### Title
Front-runnable `donateRewards()` in reward pools enables donation-dilution sandwich attack on legitimate stakers - (File: rewards/BaseRewardPool.sol / BaseRewardPoolV2.sol / mWOMSVBaseRewarder.sol)

### Summary
`donateRewards()` in the reward-pool contracts is a completely permissionless, unprivileged function that immediately mixes the donated amount into `rewardPerTokenStored` based on the *current* `totalStaked()` snapshot. Any ordinary wallet can front-run an incoming donation with a large `deposit`/`stake`, capture a disproportionate share of the donated rewards once `donateRewards` executes, and immediately `withdraw`, diluting the payout that should have accrued to the pool's pre-existing stakers. This is the same non-atomic "donate-then-value-per-share-recalculated" pattern as Arcadia's `donateToTranche` sandwich (Sherlock M-5), except here the donation entrypoint isn't even gated to a permissioned role — it's callable by any unprivileged wallet.

### Finding Description
`donateRewards` is external, with no access control beyond requiring the token already be registered as a reward token: [1](#0-0) 

It funnels straight into `_provisionReward`, which immediately recomputes `rewardPerTokenStored` proportional to `this.totalStaked()` at the time of the call: [2](#0-1) 

The same pattern exists in `BaseRewardPoolV2.sol`: [3](#0-2) 

and in `mWOMSVBaseRewarder.sol`: [4](#0-3) 

Stakers reach this pool through `MasterMagpie.deposit`/`withdraw` (or `depositFor`/`withdrawFor` invoked by a `WombatPoolHelper`), both of which are unprivileged, uncooldowned, and immediately update `user.amount`/`totalStaked()`: [5](#0-4) [6](#0-5) 

The pool-helper deposit/withdraw path used for ordinary LP staking (WombatStaking LP custody) has no timelock either: [7](#0-6) 

Because reward accrual uses a single global `rewardPerTokenStored` accumulator rather than a per-donation snapshot of who was staked *before* the donation, any stake added immediately before a `donateRewards`/`queueNewRewards` call participates fully in that donation, and can be withdrawn immediately after, exactly as in the referenced Arcadia bug.

### Impact Explanation
An attacker (or anyone observing a pending donation in the mempool) can:
1. Front-run the donation with a large `deposit` into the target pool via `MasterMagpie`/`WombatPoolHelper`.
2. Let the `donateRewards` (or `queueNewRewards`) transaction execute, which recalculates `rewardPerTokenStored` using `totalStaked()` inflated by the attacker's fresh deposit.
3. Immediately `withdraw` (harvesting rewards via `_harvestAndUnstake` and `getReward`), capturing a share of the donation proportional to their large, transient stake, while original long-term stakers receive a diluted share.

This directly siphons value away from legitimate, honest reward-pool stakers — a genuine loss-of-funds condition for unprivileged users, matching the "theft of unclaimed yield" impact category.

### Likelihood Explanation
Likelihood is bounded by the same considerations that made the Arcadia analog Medium rather than High: the attacker needs visibility into a pending donation transaction and must commit real capital (no flash loans, since deposits/withdraws touch pool state across two separate transactions/blocks and reward accrual is not atomic with staking). However, because `donateRewards` here is *not* even a permissioned/manual admin flow (unlike Arcadia's `donateToTranche`), it is more broadly triggerable — e.g., anyone compensating stakers, protocol-initiated reward top-ups via `queueNewRewards`/`WombatBribeManager` bribe distribution flows all funnel through the same vulnerable `_provisionReward` accounting, giving attackers more frequent windows to exploit than in the Arcadia case.

### Recommendation
- Snapshot `totalStaked()` (or use a checkpoint/epoch system) prior to accepting new deposits within the same block/transaction as a donation, or delay the effect of `donateRewards`/`queueNewRewards` to the next reward epoch rather than applying it immediately to `rewardPerTokenStored`.
- Alternatively, impose a minimum staking duration (timelock) before a deposit is eligible to receive newly donated/queued rewards, so freshly deposited stake cannot capture donations made in the same or nearly the same block.
- Consider requiring `donateRewards` only be usable for pro-rata distribution based on stake duration/weighted-average balance rather than instantaneous stake snapshot.

### Proof of Concept
1. Pool `P` has `totalStaked() = 100` from long-term stakers, no donation pending.
2. Attacker calls `MasterMagpie.deposit(stakingToken, 900)` → `totalStaked()` becomes `1000`, attacker owns 90% of the pool's stake.
3. A `donateRewards(1000, rewardToken)` (or `queueNewRewards`) call executes on `BaseRewardPool`/`BaseRewardPoolV2`/`mWOMSVBaseRewarder`, per `_provisionReward`:
   `rewardPerTokenStored += (1000 * 10**decimals) / 1000` — i.e., the entire reward is now split with the attacker owning 90% of it despite having staked for a single block.
4. Attacker calls `MasterMagpie.withdraw(stakingToken, 900)`, which internally calls `_harvestAndUnstake` → `_harvestBaseRewarder` → `getReward`, paying out ~900 of the 1000 donated reward tokens to the attacker, and then returns their principal.
5. Long-term stakers who owned 100% of the pool before the donation are left with only ~10% of the donation's value, despite the donation being intended for them.

### Citations

**File:** rewards/BaseRewardPool.sol (L276-284)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
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

**File:** rewards/BaseRewardPoolV2.sol (L252-313)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }

    /* ============ Admin Functions ============ */

    function updateManager(address _rewardManager, bool _allowed) external onlyOwner {
        managers[_rewardManager] = _allowed;

        emit ManagerUpdated(_rewardManager, managers[_rewardManager]);
    }

    /// @notice Sends new rewards to be distributed to the users staking. Only callable by manager
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function queueNewRewards(uint256 _amountReward, address _rewardToken)
        override
        external
        onlyManager
        returns (bool)
    {
        if (!isRewardToken[_rewardToken]) {
            rewardTokens.push(_rewardToken);
            isRewardToken[_rewardToken] = true;
        }

        _provisionReward(_amountReward, _rewardToken);
        return true;
    }

    /* ============ Internal Functions ============ */

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

**File:** rewards/mWOMSVBaseRewarder.sol (L293-327)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }    

    /* ============ Internal Functions ============ */

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

**File:** rewards/MasterMagpie.sol (L334-346)
```text
    /// @notice Deposits staking token to the pool, updates pool and distributes rewards
    /// @param _stakingToken Staking token of the pool
    /// @param _amount Amount to deposit to the pool
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

**File:** wombat/WombatPoolHelper.sol (L123-170)
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

    function harvest() external override {
        IWombatStaking(wombatStaking).harvest(lpToken);
    }

    /* ============ Internal Functions ============ */

    function _deposit(uint256 _amount, uint256 _minimumLiquidity, address _from) internal {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, msg.sender, _from);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, msg.sender);
        
        emit NewDeposit(msg.sender, _amount);
    }

    function _wrapNative() internal {
        IWNative(depositToken).deposit{value: msg.value}();
    }

    /// @notice stake the receipt token in the masterchief of GMP on behalf of the caller
    function _stake(uint256 _amount, address _sender) internal {
        IERC20(stakingToken).safeApprove(masterMagpie, _amount);
        IMasterMagpie(masterMagpie).depositFor(stakingToken, _amount, _sender);
    }

    /// @notice unstake from the masterchief of GMP on behalf of the caller
    function _unstake(uint256 _amount, address _sender) internal {
        IMasterMagpie(masterMagpie).withdrawFor(stakingToken, _amount, _sender);
    }
```

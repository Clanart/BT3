No withdrawal fee/lockup was found protecting `MasterMagpie` deposits/withdrawals, confirming the attack path is unobstructed.

### Title
Instant, Permissionlessly-Triggerable Reward Injection in `BaseRewardPool` Allows Sandwich Theft of Yield via `WombatStaking.harvest()` - (File: rewards/BaseRewardPool.sol / wombat/WombatStaking.sol)

### Summary
`WombatStaking.harvest()` is an unprivileged, externally callable function that pulls WOM/bribe rewards from Wombat Exchange and immediately pushes them into `BaseRewardPool` via `queueNewRewards()`. `BaseRewardPool._provisionReward()` instantly folds the incoming reward into `rewardPerTokenStored`, divided by whatever `totalStaked()` happens to be at that exact block — with no vesting, streaming, or time-locked distribution. Because `MasterMagpie.deposit()`/`withdraw()` have no cooldown, minimum holding period, or exit fee, an attacker can front-run (or directly trigger) `harvest()` with a large deposit, capture a disproportionate share of the freshly queued rewards, and withdraw immediately afterward — an exact analog of the sandwich attack described in the MultipliVault report, where `onUnderlyingBalanceUpdate()` instantly bumps the share price with no anti-sandwich protection.

### Finding Description
`WombatStaking.harvest(address _lpToken)` has no access control besides `whenNotPaused` and `_onlyActivePool`, meaning any wallet can call it: [1](#0-0) . It routes collected rewards to `_toMasterWomAndSendReward`, which ultimately calls `IBaseRewardPool(_rewarder).queueNewRewards(_amount, _rewardToken)` for both fee recipients and the main rewarder: [2](#0-1) .

`queueNewRewards`/`donateRewards` in `BaseRewardPool` (and identically in `BaseRewardPoolV2`, `mWOMSVBaseRewarder`, `vlMGPBaseRewarder`) call `_provisionReward`, which immediately increases `rewardPerTokenStored` proportionally to the reward amount divided by the current `totalStaked()`, with no time-based streaming (no `rewardRate`/`duration` mechanism as used elsewhere, e.g. `WomUp.sol`): [3](#0-2) . This means the entire reward is attributed at once to whichever addresses are staked at the moment `queueNewRewards` executes.

Users stake and unstake through `MasterMagpie.deposit()`/`withdraw()` with no holding-period restriction: [4](#0-3) , and the internal `_deposit`/`_withdraw` logic immediately updates `user.amount` and `rewardDebt`/reward-per-token accounting with no delay: [5](#0-4) . `donateRewards` in `BaseRewardPool` also has no access restriction at all, letting anyone directly trigger the instant `rewardPerTokenStored` bump: [6](#0-5) .

This mirrors the root cause in the external report: an instant, mempool-visible balance/reward update that directly and proportionally affects payout to current holders, with no anti-sandwich delay for deposits/withdrawals.

### Impact Explanation
Legitimate long-term LPs staked in `MasterMagpie` pools (via `WombatPoolHelper`/`WombatPoolHelperV2`/`AnkrBNBPoolHelper`) have their proportional share of harvested WOM/bribe yield diluted whenever an attacker deposits immediately before a `harvest()` call (which the attacker can even trigger themselves, since `harvest()` is permissionless) and withdraws immediately after. This is a direct theft of unclaimed yield from honest stakers, redirected to the attacker with no economic contribution to the pool, satisfying "theft of unclaimed yield."

### Likelihood Explanation
High. `harvest()` and `donateRewards()` are unauthenticated and callable by any wallet, removing any need to wait for or front-run an operator transaction — the attacker fully controls the timing. `MasterMagpie.deposit`/`withdraw` have no cooldown, minimum stake duration, or withdrawal fee, so the full deposit→harvest→withdraw sequence can be executed atomically or within one block by an ordinary EOA/contract, exactly as in the referenced MultipliVault exploit path.

### Recommendation
Introduce time-weighted reward streaming (e.g., a `rewardRate`/`periodFinish` model, as already used in `WomUp.sol`) instead of instantaneously crediting the entire `_amountReward` to `rewardPerTokenStored` in `_provisionReward`. Additionally, add a minimum staking duration or withdrawal cooldown in `MasterMagpie` before newly deposited balances become eligible for freshly queued rewards, and consider restricting `donateRewards`/`harvest` triggering incentives so they cannot be timed by an attacker's own transaction.

### Proof of Concept
1. Attacker monitors mempool or directly prepares to call `WombatStaking.harvest(lpToken)` (permissionless, `wombat/WombatStaking.sol` L331-335).
2. In the preceding transaction (or same block if ordering allows), attacker calls `WombatPoolHelper.deposit()` → `MasterMagpie.depositFor()` → `_deposit()`, staking a large LP amount just before harvest (`rewards/MasterMagpie.sol` L482-505).
3. Attacker calls (or lets bot call) `WombatStaking.harvest(lpToken)`, which calls `_toMasterWomAndSendReward` → `IBaseRewardPool(rewarder).queueNewRewards(amount, rewardToken)` (`wombat/WombatStaking.sol` L755-770), instantly increasing `rewardPerTokenStored` in `BaseRewardPool._provisionReward` (`rewards/BaseRewardPool.sol` L297-320) based on `totalStaked()` inflated by the attacker's deposit.
4. Attacker immediately calls `WombatPoolHelper.withdraw()` → `MasterMagpie.withdrawFor()`, which harvests the pro-rata reward share for the attacker's inflated stake before unstaking, with no fee or delay penalizing the short holding period.
5. Net effect: attacker captures reward proportional to their large but momentary stake, diluting the payout that would otherwise accrue to genuine long-term stakers.

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

**File:** wombat/WombatStaking.sol (L755-770)
```text
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

**File:** rewards/BaseRewardPool.sol (L297-320)
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

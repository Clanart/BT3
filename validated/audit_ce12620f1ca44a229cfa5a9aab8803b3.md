### Title
Reward distribution via `queueNewRewards`/`donateRewards` can be frontrun by deposits, diluting rewards for existing stakers - (File: rewards/BaseRewardPoolV2.sol)

### Summary
`BaseRewardPoolV2` (and its sibling `BaseRewardPool.sol` / `mWOMSVBaseRewarder.sol`) distributes rewards to stakers using a global, non-time-weighted `rewardPerTokenStored` index that is bumped instantaneously whenever new rewards are provisioned via `queueNewRewards()` or the permissionless `donateRewards()`. Unlike the referenced Merit Circle codebase, this codebase has no `MIN_LOCK_DURATION` or any other time-weighting/vesting mechanism protecting reward distribution. Any wallet can watch the mempool for an upcoming reward-provisioning transaction (or trigger one itself via harvest/donate), front-run it with a large `MasterMagpie.deposit()`/`depositFor()` call, and immediately be entitled to a proportional share of the newly added rewards despite having contributed zero time or risk to the pool, then withdraw right after.

### Finding Description
Reward accounting in `BaseRewardPoolV2` is a simple cumulative-index (MasterChef-style) model: [1](#0-0) 

`_provisionReward()` updates `rewardPerTokenStored` based on the **current** `totalStaked()` at the moment new rewards are queued — there is no snapshot of "who was staked for how long" or any minimum holding period before a deposit can start earning: [2](#0-1) 

`_earned`/`updateRewards` compute rewards purely from the account's current staked balance multiplied by the delta in `rewardPerTokenStored` since the last checkpoint, with no vesting or delay: [3](#0-2) 

Deposits in `MasterMagpie._deposit()` take effect immediately with no cooldown before the new stake counts toward `balanceOf()` (which reads live stake from `MasterMagpie`): [4](#0-3) 

Reward provisioning itself can be triggered in multiple predictable ways: a manager-only `queueNewRewards()` (e.g. periodic emissions from `WombatStaking._sendRewards`), or the fully permissionless `donateRewards()` that anyone can call to inject rewards for an already-registered token: [5](#0-4) 

`WombatStaking` also converts and forwards harvested WOM/LP fees into these reward pools via `queueNewRewards`, and pool helpers like `WombatPoolHelper.harvest()` are callable by any external account, making the timing of reward injections observable/triggerable in the mempool: [6](#0-5) [7](#0-6) 

Because there is no analog to Merit Circle's `MIN_LOCK_DURATION` anywhere in this codebase (confirmed via repo-wide search — no lock-duration/cooldown constant exists for the reward pools), an attacker can:
1. Observe (or itself submit) a transaction that will call `queueNewRewards`/`donateRewards`/harvest with a non-trivial reward amount.
2. Front-run it with a large `deposit()`/`depositFor()` into the corresponding pool via `MasterMagpie`.
3. Let the reward-provisioning transaction execute, immediately becoming entitled to a proportional share of the newly distributed rewards.
4. Call `withdraw()` right after to exit, having captured yield that rightfully belonged to genuine long-term stakers, diluting their rewards.

### Impact Explanation
This directly dilutes/steals unclaimed yield from legitimate long-term stakers in favor of an opportunistic depositor who bears no time-at-risk in the pool. Every deposit made to any `BaseRewardPoolV2`/`BaseRewardPool`/`mWOMSVBaseRewarder`-backed pool is exposed to this pattern, since the underlying reward math and `MasterMagpie` deposit flow contain no protective lock or vesting delay.

### Likelihood Explanation
Likelihood is high for a determined actor: reward injections (`queueNewRewards` calls resulting from `WombatStaking` harvests, or the permissionless `donateRewards()`) are visible in the mempool, deposits are unrestricted and immediate, and withdrawals have no cooldown either, allowing rapid in-and-out execution within a single block or a couple of blocks via standard MEV techniques.

### Recommendation
Introduce a minimum staking/lock duration (or a time-weighted checkpoint mechanism) before a deposit becomes eligible to earn newly queued rewards in `BaseRewardPoolV2`/`BaseRewardPool`/`mWOMSVBaseRewarder`, and/or use a private relay for reward-distribution transactions to reduce mempool visibility. Consider restricting or rate-limiting the permissionless `donateRewards()` entry point as well.

### Proof of Concept
1. Attacker monitors mempool for a pending transaction calling `WombatStaking`/pool-manager code that will call `queueNewRewards()` on a target `BaseRewardPoolV2`, or attacker directly calls the permissionless `donateRewards()` themselves after depositing.
2. Attacker calls `MasterMagpie.deposit(stakingToken, largeAmount)` — [8](#0-7)  — which immediately sets `user.rewardDebt` based on the pre-distribution `accMGPPerShare`/`rewardPerTokenStored`.
3. The reward-provisioning transaction executes, calling `_provisionReward()` which raises `rewardPerTokenStored` for all currently staked balances including the attacker's freshly deposited amount — [9](#0-8) .
4. Attacker calls `getReward`/`getRewards` via `MasterMagpie` claim path to collect the proportional reward share, then `withdraw()` to exit the pool, having captured yield diluted from other stakers without any holding-period risk.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L126-152)
```text
    function totalStaked() public override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }

    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }

    function stakingDecimals() external override virtual view returns (uint256) {
        return stakingTokenDecimals;
    }

    /// @notice Returns amount of reward token per staking tokens in pool
    /// @param _rewardToken Address reward token
    /// @return Returns amount of reward token per staking tokens in pool
    function rewardPerToken(address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return rewards[_rewardToken].rewardPerTokenStored;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L252-260)
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

**File:** rewards/BaseRewardPoolV2.sol (L316-321)
```text
    function _earned(address _account, address _rewardToken, uint256 _userShare) internal view returns (uint256) {
        return ((_userShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**stakingTokenDecimals) + userRewards[_rewardToken][_account];
    }
```

**File:** rewards/MasterMagpie.sol (L337-339)
```text
    function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _deposit(_stakingToken, msg.sender, _amount, false);
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

**File:** wombat/WombatStaking.sol (L755-769)
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
```

**File:** wombat/WombatPoolHelper.sol (L142-144)
```text
    function harvest() external override {
        IWombatStaking(wombatStaking).harvest(lpToken);
    }
```

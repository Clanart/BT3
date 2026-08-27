### Title
Unprivileged reward-per-token snipe on `WombatStaking::harvest` lets an attacker steal freshly-harvested WOM yield from other stakers - ([File: wombat/WombatStaking.sol])

### Summary
`WombatStaking::harvest` is a fully permissionless, unprivileged function that pulls pending WOM (and bonus) rewards from Wombat's `masterWombat` and queues them into the pool's `BaseRewardPool`/`BaseRewardPoolV2` via `queueNewRewards`, which increases `rewardPerTokenStored` in one atomic step proportional to the *current* total staked amount in `MasterMagpie`. Because a depositor's `userRewardPerTokenPaid` checkpoint is only set on deposit (before the balance increase) and `totalStaked()` is read live at harvest time, an attacker can deposit immediately before calling (or being front-run by) `harvest()`, receive a pro-rata share of yield that accrued while they held no stake, and withdraw right after, diluting the rewards legitimately earned by existing stakers.

### Finding Description
The reward-accrual math lives in `BaseRewardPool`/`BaseRewardPoolV2`:
- `totalStaked()` reads the live staking-token balance held by `MasterMagpie` (`operator`), not a time-weighted balance: [1](#0-0) 
- `_provisionReward` (invoked from `queueNewRewards`) increases `rewardPerTokenStored` by `_amountReward * 1e_decimals / totalStaked()` in a single atomic step at the moment rewards are queued: [2](#0-1) 
- A user's checkpoint (`userRewardPerTokenPaid`) is only updated when `MasterMagpie` calls `rewarder.updateFor(_account)` inside `_harvestBaseRewarder`, which happens on `_deposit`/`_withdraw` *before* the staked amount changes: [3](#0-2) [4](#0-3) 

Because `earned()` is computed purely as `balanceOf(_account) * (rewardPerToken - userRewardPerTokenPaid)`, with no minimum staking duration or vesting, any account can:
1. Deposit into the pool via a pool helper (e.g. `WombatPoolHelper::deposit` / `depositLP`), which checkpoints them at the *pre-harvest* `rewardPerTokenStored`. [5](#0-4) 
2. Call the fully permissionless `WombatStaking::harvest(_lpToken)` — it is only gated by `_onlyActivePool`, not by any privileged role, so any wallet can trigger it at will: [6](#0-5) 
This routes into `_toMasterWomAndSendReward` → `_sendRewards` → `queueNewRewards`, which jumps `rewardPerTokenStored` up based on `totalStaked()` that now includes the attacker's freshly deposited balance: [7](#0-6) 
3. Claim the newly credited reward (via `multiclaim`/`getReward`) and withdraw the principal immediately, having captured a slice of yield that accrued to the pool over the whole period since the last harvest, despite having staked for only a few blocks.

This mirrors the reported bug class exactly: a state-changing operation causes a step increase in a value-per-share/asset accounting variable, and depositing immediately before that operation lets an attacker capture value that should belong to existing, longer-term participants — except here the triggering function (`harvest`) requires no privileged role at all, so the attack is fully self-serve by an ordinary wallet with no need to front-run an owner transaction.

### Impact Explanation
Existing long-term stakers have their unclaimed WOM (and bonus) yield diluted every time an attacker deposits shortly before triggering (or observing) a `harvest()` call and withdraws shortly after. This is a direct, repeatable theft of unclaimed yield from other users, executable by any unprivileged wallet at will since `harvest()` has no access control beyond the pool being active.

### Likelihood Explanation
High. `harvest()` is `external` and callable by anyone once a pool is active; no timing coincidence with an admin transaction is required — the attacker fully controls both the deposit and the harvest trigger, and can repeat the strategy on every reward accumulation cycle for any Wombat pool integrated with `WombatStaking`.

### Recommendation
Introduce a minimum staking/holding period (or checkpoint-and-vest mechanism) before newly deposited stake becomes eligible for freshly queued rewards, or accrue rewards to a time-weighted average balance instead of the live snapshot balance used at the moment of `queueNewRewards`. Alternatively, restrict who can trigger `harvest()` to a keeper/manager role with a cooldown, or apply a deposit fee/lockup that neutralizes the snipe.

### Proof of Concept
1. Existing stakers have staked LP receipt tokens in `MasterMagpie` for `stakingToken`, accruing pending WOM yield in `poolInfo.rewarder`, but rewards have not yet been harvested from Wombat (`rewardPerTokenStored` stale).
2. Attacker calls `WombatPoolHelper::depositLP`/`deposit`, which calls `MasterMagpie::depositFor`; this checkpoints attacker's `userRewardPerTokenPaid` at the stale `rewardPerTokenStored` [3](#0-2) .
3. Attacker (or anyone) calls `WombatStaking::harvest(lpToken)` [6](#0-5) , which harvests pending WOM and calls `queueNewRewards`, bumping `rewardPerTokenStored` based on `totalStaked()` that now includes attacker's balance [2](#0-1) .
4. Attacker calls `multiclaim`/`getReward` to receive a pro-rata share of the newly harvested WOM, then calls `WombatPoolHelper::withdraw` to exit their principal, having captured yield with near-zero holding time at the expense of other stakers' rightful share.

### Citations

**File:** rewards/BaseRewardPool.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
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

**File:** rewards/MasterMagpie.sol (L482-498)
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
```

**File:** rewards/MasterMagpie.sol (L631-636)
```text
    /// only update the reward counting on in base rewarder but not sending them to user
    function _harvestBaseRewarder(address _stakingToken, address _account) internal {
        IBaseRewardPool rewarder = IBaseRewardPool(tokenToPoolInfo[_stakingToken].rewarder);
        if (address(rewarder) != address(0))
            rewarder.updateFor(_account);
    }
```

**File:** wombat/WombatPoolHelper.sol (L96-109)
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
```

**File:** wombat/WombatStaking.sol (L329-336)
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

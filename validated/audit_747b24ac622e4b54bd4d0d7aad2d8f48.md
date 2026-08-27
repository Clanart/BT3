### Title
Permissionless `WombatStaking.harvest()` enables just-in-time deposit/withdraw reward sniping in `BaseRewardPoolV2` lump-sum reward-per-token distribution - ([File: rewards/BaseRewardPoolV2.sol])

### Summary
`WombatStaking.harvest(_lpToken)` is callable by any address, gated only by `whenNotPaused` and `_onlyActivePool(_lpToken)`, not by caller identity. [1](#0-0) . Because `BaseRewardPoolV2._provisionReward` distributes newly harvested WOM/bonus rewards as a lump-sum increment to `rewardPerTokenStored` based on `totalStaked()` at the exact moment the harvest lands, an attacker can deposit into a pool, force an immediate `harvest()`, and withdraw right after, capturing a pro-rata share of rewards that accrued in Wombat's `MasterWombat` over a period during which the attacker held no stake at all.

### Finding Description
Reward accrual in the underlying Wombat `MasterWombat` protocol is continuous and time-based, but it is only converted into `BaseRewardPoolV2` accounting in discrete lumps whenever `_toMasterWomAndSendReward` executes and calls `IBaseRewardPool(_rewarder).queueNewRewards(...)` [2](#0-1) [3](#0-2) .

In `_provisionReward`, the entire harvested amount is divided by `totalStaked()` *at that instant* and added to `rewardPerTokenStored`: [4](#0-3) 

`totalStaked()` and `balanceOf()` read live balances from `MasterMagpie.stakingInfo`, with no time-weighting or vesting: [5](#0-4) .

Exploit flow:
1. Attacker deposits into the Wombat pool via a `PoolHelper` → `WombatStaking.deposit` → `MasterMagpie.depositFor`. The internal harvest inside `deposit()` runs *before* the receipt token is minted, so the attacker is not yet in `totalStaked()` for that harvest [6](#0-5) . After minting, `_stake` registers the attacker's balance in `BaseRewardPoolV2` via `MasterMagpie.depositFor` → `_deposit` [7](#0-6) .
2. Attacker (or anyone) calls `WombatStaking.harvest(_lpToken)` directly — no permission required — which pulls all WOM accrued in `MasterWombat` since the last harvest and pushes it into `BaseRewardPoolV2.rewardPerTokenStored`, distributed pro-rata over *current* `totalStaked()`, which now includes the attacker's freshly deposited stake alongside long-term stakers [1](#0-0) .
3. Attacker withdraws immediately via `PoolHelper.withdraw` → `WombatStaking.withdraw` (which itself also triggers a harvest before the attacker's stake is removed) → `MasterMagpie.withdrawFor` → `_harvestAndUnstake`, which calls `_harvestBaseRewarder` (crystallizing the attacker's `userRewards` at the just-inflated `rewardPerTokenStored`) before decrementing `user.amount` [8](#0-7) , [9](#0-8) .

No cooldown, lock period, or withdrawal fee gates this deposit→harvest→withdraw sequence in `MasterMagpie._deposit`/`_withdraw` or `BaseRewardPoolV2`, so the whole cycle can complete in a handful of blocks. The `updateReward`/`updateFor` mechanism correctly checkpoints balances at every stake change, but it has no defense against the underlying design flaw: reward realization is lumpy (tied to discrete harvest events) rather than continuously streamed, so whoever is staked *at the harvest instant* captures the whole jump regardless of how long they were actually exposed to the pool.

### Impact Explanation
This allows theft of unclaimed yield belonging to long-term stakers: an attacker can appear only for the duration of one harvest cycle and take a share of rewards proportional to their momentary balance rather than their time-weighted stake, diluting the payout genuine long-term LPs would otherwise receive. This matches the "theft of unclaimed yield" impact category. The magnitude of capture scales with the attacker's stake size relative to total pool TVL, so it is most damaging against pools with low TVL or long gaps between organic harvest-triggering deposits/withdrawals, where a sizeable accrued WOM balance is waiting in `MasterWombat` to be swept in one lump.

### Likelihood Explanation
The attack requires only real capital that the attacker already controls (Wombat LP deposit tokens), no privileged role, and no flash loan (capital must sit in the pool across the deposit→harvest→withdraw sequence, though this can be minimized to a few blocks/transactions since there is no lock-up). It is fully repeatable every time a meaningful amount of WOM has accrued unharvested in `MasterWombat`, and the attacker can monitor pending WOM off-chain to time the attack for maximum effect. The main capital cost/risk is proportional exposure to Wombat pool slippage on deposit/withdraw, which is typically small for stable pools.

### Recommendation
- Restrict `harvest()` to trusted callers only (e.g., `poolManager`/keeper), removing public callability, so the timing of lump-sum reward realization cannot be gamed by third parties.
- More fundamentally, redesign reward distribution to avoid lump-sum sensitivity to instantaneous `totalStaked()`: e.g., stream harvested rewards linearly over the elapsed harvest interval instead of crediting them entirely to whoever holds the current balance, or add a minimum staking duration / harvest cooldown after deposit before a user's new stake becomes eligible for freshly harvested rewards.

### Proof of Concept
Foundry test plan:
1. Deploy `WombatStaking`, `MasterMagpie`, `BaseRewardPoolV2`, and a mocked `MasterWombat`/`WombatPool` that accrues WOM linearly per second per LP staked.
2. Have "LongTermStaker" deposit a large LP stake and let significant time pass (simulate `vm.warp`) so a large pending WOM balance accrues in the mock `MasterWombat`, unharvested.
3. Attacker deposits a small stake via `PoolHelper.deposit`.
4. Attacker calls `WombatStaking.harvest(_lpToken)` directly (no special role) in the next block.
5. Attacker immediately calls `PoolHelper.withdraw` to exit and claim reward via `MasterMagpie.claim`/`getReward`.
6. Assert: `attackerRewardReceived / attackerStakeDuration` ratio is orders of magnitude greater than `longTermStaker's earned reward / their stake duration` for the same harvested lump, demonstrating disproportionate capture of yield relative to time-weighted stake, and that `LongTermStaker`'s later claim is reduced by exactly the amount diverted to the attacker (conservation-violating transfer of accrued yield).

### Citations

**File:** wombat/WombatStaking.sol (L248-270)
```text
    ) nonReentrant whenNotPaused _onlyActivePoolHelper(_lpAddress) external {
        // Get information of the Pool of the token
        Pool storage poolInfo = pools[_lpAddress];
        address depositToken = poolInfo.depositToken;
        IERC20(depositToken).safeTransferFrom(_from, address(this), _amount);

        IERC20(depositToken).safeApprove(poolInfo.depositTarget, _amount);
        uint256 beforeBalance = IERC20(poolInfo.lpAddress).balanceOf(address(this));
        IWombatPool(poolInfo.depositTarget).deposit(
            depositToken,
            _amount,
            _minimumLiquidity,
            address(this),
            block.timestamp,
            false
        );

        uint256 lpReceived = IERC20(poolInfo.lpAddress).balanceOf(address(this)) - beforeBalance;
        _toMasterWomAndSendReward(_lpAddress, lpReceived, true); // triggers harvest from wombat exchange
        // update variables
        IMintableERC20(poolInfo.receiptToken).mint(msg.sender, lpReceived);
        emit NewDeposit(_for, depositToken, _amount, poolInfo.receiptToken, lpReceived);
    }
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

**File:** wombat/WombatStaking.sol (L767-769)
```text
        IERC20(_rewardToken).safeApprove(_rewarder, 0);
        IERC20(_rewardToken).safeApprove(_rewarder, _amount);
        IBaseRewardPool(_rewarder).queueNewRewards(_amount, _rewardToken);
```

**File:** rewards/BaseRewardPoolV2.sol (L124-136)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
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
```

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

**File:** rewards/MasterMagpie.sol (L516-534)
```text
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

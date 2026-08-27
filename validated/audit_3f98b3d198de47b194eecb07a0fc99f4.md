### Title
Reward distribution in `BaseRewardPool`/`BaseRewardPoolV2` can be front-run and stolen via instant `deposit`/`withdraw` with no lock period - (File: rewards/BaseRewardPool.sol, rewards/BaseRewardPoolV2.sol, rewards/MasterMagpie.sol)

### Summary
`BaseRewardPool._provisionReward` (invoked via `queueNewRewards`, `donateRewards`, or bribe payouts from `WombatStaking._toMasterWomAndSendReward`) instantly bakes newly received rewards into `rewardPerTokenStored` based on the current `totalStaked()` snapshot. Because `MasterMagpie.deposit`/`withdraw` have no cooldown, minimum lock, or vesting period, an attacker can front-run any reward-injecting transaction with a large `deposit`, receive a pro-rata share of rewards that were earned entirely by other, longer-term stakers, and immediately `withdraw` for a profit.

### Finding Description
`_provisionReward` recalculates `rewardInfo.rewardPerTokenStored` in one shot proportional to `totalStaked()` at the moment the reward is provisioned: [1](#0-0) 

This function is reachable from `queueNewRewards` (called by any address in the `managers` mapping, including `WombatStaking` when it harvests/distributes WOM/bribe rewards) and from the fully permissionless `donateRewards`: [2](#0-1) 

`MasterMagpie._deposit`/`_withdraw` have no lock time, cooldown, or streaming mechanism — a user can deposit and withdraw in the same block/transaction sequence, harvesting the `BaseRewardPool` position both times: [3](#0-2) 

`WombatStaking` regularly funnels harvested WOM rewards and bribe rewards into the pool's rewarder via `queueNewRewards`, which is a visible, predictable, mempool-observable event: [4](#0-3) 

Because there is no time-weighted/streaming distribution (e.g., a `periodFinish`/`rewardRate` style linear vesting as used in Synthetix `StakingRewards`), an attacker watching the mempool for a `queueNewRewards`/`donateRewards`/bribe-harvest transaction can:
1. Front-run it with a large `deposit(_stakingToken, _amount)` into `MasterMagpie`, inflating `totalStaked()` immediately before the reward injection.
2. Let the reward transaction execute, which updates `rewardPerTokenStored` pro-rata across the now-inflated `totalStaked()`.
3. Immediately call `withdraw` (via `_withdraw` → `_harvestBaseRewarder` → `BaseRewardPool.getReward`), collecting a share of the reward proportional to their deposit despite having staked for virtually zero time, and remove their principal.

This is the same root cause as the referenced NFTX finding: instant, lump-sum reward distribution to a `totalStaked()`-weighted pool with no minimum staking duration.

### Impact Explanation
Genuine long-term stakers of `MasterMagpie` pools (via `WombatPoolHelper`/`WombatPoolHelperV2`/`AnkrBNBPoolHelper`) have their earned WOM/bribe rewards diluted and effectively stolen by an attacker who deposits capital for a single block. This is a direct theft of yield that rightfully belongs to existing stakers, satisfying the "theft or permanent freezing of unclaimed yield" impact bar.

### Likelihood Explanation
Reward injections (`queueNewRewards` from `WombatStaking` harvest/bribe flows, or permissionless `donateRewards`) are regular, predictable, and visible in the mempool. Any unprivileged wallet with sufficient capital (potentially flash-loanable, since the deposited token itself is not consumed, only temporarily supplied) can execute this front-run/back-run pattern with ordinary transactions and no special privileges, making exploitation straightforward and repeatable each time rewards are distributed.

### Recommendation
Distribute rewards over time (streaming/linear vesting via a `rewardRate` and `periodFinish`, analogous to Synthetix `StakingRewards`) instead of crediting the entire `_amountReward` to `rewardPerTokenStored` in a single `_provisionReward` call. Additionally, consider adding a minimum staking duration or withdrawal cooldown in `MasterMagpie` to prevent same-block deposit/withdraw reward extraction.

### Proof of Concept
1. Attacker monitors mempool for a pending `IBaseRewardPool(rewarder).queueNewRewards(rewardAmount, rewardToken)` call (e.g., triggered from `WombatStaking._toMasterWomAndSendReward`, `rewards/WombatStaking.sol:411`) or a `donateRewards` call.
2. Attacker submits `MasterMagpie.deposit(_stakingToken, largeAmount)` with higher gas price so it lands before the reward transaction — see `rewards/MasterMagpie.sol:337-339` and `_deposit` at `rewards/MasterMagpie.sol:482-505`, which has no lock/vesting logic.
3. The reward transaction executes `_provisionReward`, immediately increasing `rewardInfo.rewardPerTokenStored` proportional to the now-inflated `totalStaked()` — `rewards/BaseRewardPool.sol:297-318`.
4. Attacker calls `MasterMagpie.withdraw(_stakingToken, largeAmount)`, which internally calls `_harvestBaseRewarder` → `BaseRewardPool.getReward`, paying out the attacker's pro-rata share of the just-injected reward, then returns their full principal — `rewards/MasterMagpie.sol:508-514`, `rewards/BaseRewardPool.sol:219-240`.
5. Attacker exits with profit extracted from rewards meant for pre-existing stakers, having held the position for effectively zero time.

### Citations

**File:** rewards/BaseRewardPool.sol (L258-284)
```text
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

    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
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

**File:** wombat/WombatStaking.sol (L408-412)
```text
                        rewardAmount -= protocolFee;
                        rewardAmount -= callerFeeAmount;
                        IERC20(rewardTokens[i][j]).safeApprove(_rewarders[i], rewardAmount);
                        IBaseRewardPool(_rewarders[i]).queueNewRewards(rewardAmount, address(rewardTokens[i][j]));
                    }
```

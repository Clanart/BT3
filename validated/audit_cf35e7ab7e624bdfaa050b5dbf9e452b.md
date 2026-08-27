### Title
Reward-per-token accounting in `BaseRewardPool`/`BaseRewardPoolV2` can be diluted by direct token transfers to `MasterMagpie` - ([File: rewards/BaseRewardPool.sol], [File: rewards/BaseRewardPoolV2.sol])

### Summary
`BaseRewardPool.totalStaked()` (and the identical `BaseRewardPoolV2.totalStaked()`) compute the pool's staked amount from the live ERC20 balance of the staking token held by `MasterMagpie`, rather than from `MasterMagpie`'s own internal accounting (`userInfo[...].amount`). Any unprivileged wallet can inflate this balance by directly transferring the staking token to the `MasterMagpie` contract, without going through `deposit`/`depositFor`. This mirrors the reported Caviar `Pair` bug where `_baseTokenReserves()`/`fractionalTokenReserves()` are derived from `balanceOf(address(this))` and can be manipulated by direct transfers, bypassing the internal invariant.

### Finding Description
`totalStaked()` is defined as: [1](#0-0) [2](#0-1) 

This value is used as the denominator when new rewards are queued: [3](#0-2) [4](#0-3) 

Meanwhile, the actual per-user stake is tracked separately and independently in `MasterMagpie.userInfo[_stakingToken][_account].amount`, updated only through `_deposit`/`_withdraw`: [5](#0-4) [6](#0-5) 

Because `totalStaked()` reads the raw `balanceOf(operator)` instead of a value updated only on legitimate deposits/withdrawals, any ordinary wallet can send staking tokens directly to `MasterMagpie`'s address. This inflates the denominator used in `_provisionReward` without registering any offsetting `user.amount`, so `rewardPerTokenStored` is computed against an artificially larger supply.

### Impact Explanation
Every subsequent call to `queueNewRewards`/`donateRewards` computes `rewardPerTokenStored += (_amountReward * 10**decimals) / totalStaked()`. With an inflated `totalStaked()`, the reward-per-token credited to real stakers is permanently reduced for that reward distribution — the diluted portion is not recoverable by legitimate stakers because it was silently absorbed into a denominator with no owner. Since a direct transfer permanently increases `operator`'s balance (there's no mechanism to reclaim or exclude it from `totalStaked()`), the dilution effect is permanent for future distributions until/unless that donated balance is somehow withdrawn — which the pool has no path to do, since deposits are exclusively tracked via `userInfo`. This results in a permanent reduction/freezing of unclaimed yield for all real stakers in that pool.

### Likelihood Explanation
The action requires only a plain ERC20 `transfer` call from any wallet to the `MasterMagpie` contract address for the given staking token — no privileged role, no governance, and no interaction with an external protocol/oracle is needed. The attacker's own tokens are lost/locked (since there's no accounting crediting them), but this is a low-cost griefing vector against all other stakers' future rewards, consistent with a validated, reachable path from an ordinary wallet.

### Recommendation
Track staked supply with an internal state variable in `BaseRewardPool`/`BaseRewardPoolV2`, incremented/decremented only within `stake`/`withdraw` (called from `MasterMagpie`'s deposit/withdraw flow), rather than deriving `totalStaked()` from `IERC20(stakingToken).balanceOf(operator)`. This removes the ability of direct transfers to affect reward-per-token calculations, analogous to Uniswap V2's internal `reserve0`/`reserve1` state variables recommended in the referenced report.

### Proof of Concept
1. A pool exists in `MasterMagpie` for `stakingToken` X with legitimate stakers holding a combined `userInfo` amount of `S`.
2. `BaseRewardPool.totalStaked()` currently returns `S` because `IERC20(X).balanceOf(masterMagpie) == S`.
3. Attacker (unprivileged wallet) calls `X.transfer(masterMagpie, D)` directly, with no call to `deposit`/`depositFor`. No `userInfo` entry is created or modified.
4. `totalStaked()` now returns `S + D`.
5. A reward provisioner calls `queueNewRewards`/`donateRewards` (via `WombatStaking._sendRewards` or manager flows) with reward amount `R` for this pool: `rewardPerTokenStored += (R * 10**decimals) / (S + D)` instead of `(R * 10**decimals) / S`.
6. Every legitimate staker's `earned()` value, computed against `rewardPerTokenStored`, is permanently reduced relative to what it should have been, for that and any subsequent reward-per-token increments while the inflated balance persists — matching the "theft or permanent freezing of unclaimed yield" impact category. [7](#0-6)

### Citations

**File:** rewards/BaseRewardPool.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
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

**File:** rewards/BaseRewardPoolV2.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() public override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L301-313)
```text
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

**File:** rewards/MasterMagpie.sol (L260-266)
```text
    function stakingInfo(address _stakingToken, address _user)
        public
        view
        returns (uint256 stakedAmount, uint256 availableAmount)
    {
        return (userInfo[_stakingToken][_user].amount, userInfo[_stakingToken][_user].available);
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

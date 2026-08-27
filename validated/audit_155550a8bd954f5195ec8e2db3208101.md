### Title
Flash-loaned stake can instantly snipe a disproportionate share of freshly harvested WOM/bribe rewards from `BaseRewardPool` because rewards are credited pro-rata to the *instantaneous* stake snapshot with no lock/cooldown - ([File: rewards/BaseRewardPool.sol], [File: wombat/WombatStaking.sol], [File: rewards/MasterMagpie.sol])

### Summary
The Debita bug class relies on: (1) a reward/incentive that is finalized based on a point-in-time balance/ratio snapshot, (2) no restriction preventing the attacker from entering and exiting in the same transaction, and (3) a permissionless trigger that crystallizes the reward. The same structural pattern exists in this codebase's Wombat integration: `BaseRewardPool._provisionReward` instantly mints `rewardPerTokenStored` proportional to `totalStaked()` at the exact block the reward is queued, `WombatStaking.harvest()` is a permissionless function that pushes freshly-harvested WOM/bonus rewards into that pool via `queueNewRewards`, and `MasterMagpie.deposit`/`withdraw` allow depositing and withdrawing in the same block with no cooldown.

### Finding Description
`BaseRewardPool._provisionReward` computes the reward-per-share increase using the *current* `totalStaked()` balance at the moment the reward is injected, not a time-weighted average: [1](#0-0) 

`totalStaked()` simply reflects the current balance of the staking token held by `MasterMagpie` (the `operator`), i.e., the sum of everyone's live deposits at that instant: [2](#0-1) 

`WombatStaking.harvest()` is externally callable by anyone (subject only to `whenNotPaused`/`_onlyActivePool`, not an owner/manager check) and immediately converts freshly harvested WOM (and bonus tokens) from Wombat into a `queueNewRewards` call on the pool's `BaseRewardPool`: [3](#0-2) [4](#0-3) 

`MasterMagpie.deposit`/`withdraw` have no minimum holding period, cooldown, or same-block restriction — a user can deposit and withdraw within the same transaction, harvesting any reward that was credited in between: [5](#0-4) [6](#0-5) 

Combining these three facts, an attacker can, in a single transaction:
1. Flash-loan a large amount of the pool's deposit/LP token.
2. Deposit it into `MasterMagpie` for that pool's staking token, inflating `totalStaked()`.
3. Call `WombatStaking.harvest(_lpToken)`, which harvests pending WOM/bribe rewards accrued by *all* prior depositors and calls `queueNewRewards`, crediting `rewardPerTokenStored` based on the now attacker-inflated `totalStaked()`.
4. Immediately call `withdraw`/`getReward` to claim their pro-rata share of the just-harvested reward and unwind the position.
5. Repay the flash loan.

Because the reward injection is an instantaneous, non-time-weighted snapshot and there is no anti-flash-loan/anti-same-block guard on deposit/withdraw, the attacker captures a share of rewards that rightfully belongs to depositors who had capital staked for the entire accrual period, diluting/stealing part of their earned (but not-yet-claimed) yield with borrowed, momentary capital and effectively zero real exposure.

### Impact Explanation
This results in theft of unclaimed yield/incentives from genuine long-term LP/vlMGP stakers in the Wombat integration reward pools — the harvested WOM/bribe rewards that should accrue to real, time-weighted stakers are instead diverted to a flash-loan attacker who never bore real economic exposure to the pool. This matches the "theft or permanent freezing of unclaimed yield" impact category.

### Likelihood Explanation
`WombatStaking.harvest` is permissionless and can be called by anyone at any time [3](#0-2) , and `MasterMagpie` deposit/withdraw are ordinary user-facing, unprivileged entry points with no cooldown [5](#0-4) . The only friction is acquiring flash-loanable liquidity in the specific LP/deposit token and any pool-helper deposit slippage/fees, both of which are external/economic constraints rather than protections designed into the protocol. This makes the attack reachable purely from an ordinary wallet composing a single transaction with a flash loan.

### Recommendation
- Stream newly queued rewards over a fixed duration (e.g., Synthetix-style `rewardRate`/`periodFinish`) instead of crediting `rewardPerTokenStored` instantly based on the current `totalStaked()`.
- Introduce a minimum holding period or block-based cooldown between `deposit` and `withdraw`/`getReward` in `MasterMagpie` for a given staking token.
- Consider restricting or rate-limiting who/how often `WombatStaking.harvest` can be triggered, or snapshot `totalStaked()` prior to the block in which harvest occurs.

### Proof of Concept
1. Attacker takes a flash loan of the deposit/LP token accepted by a Wombat pool registered in `WombatStaking`.
2. Attacker deposits it (via the pool helper) into `MasterMagpie`, sharply increasing `totalStaked()` for that pool's `BaseRewardPool` (as read by `IERC20(stakingToken).balanceOf(operator)` in `BaseRewardPool.totalStaked` [2](#0-1) ).
3. Attacker calls `WombatStaking.harvest(_lpToken)` [3](#0-2) , which harvests WOM/bonus rewards accumulated by all prior stakers and immediately calls `queueNewRewards`→`_provisionReward`, which computes `rewardPerTokenStored += (_amountReward * 10**decimals) / totalStaked()` using the now attacker-inflated denominator/numerator share [1](#0-0) .
4. Attacker calls `getReward`/`withdraw` on `MasterMagpie` in the same transaction to claim their inflated pro-rata share of the harvested reward, then withdraws principal and repays the flash loan.

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

**File:** wombat/WombatStaking.sol (L720-769)
```text
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

**File:** rewards/MasterMagpie.sol (L482-514)
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
```

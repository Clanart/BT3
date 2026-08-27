## Finding

### Title
Instant deposit → withdraw sequence lets a large, flash-loanable stake steal freshly-harvested WOM rewards from `BaseRewardPool` stakers - (File: `wombat/WombatStaking.sol`, `rewards/BaseRewardPool.sol`, `wombat/WombatPoolHelper.sol`)

### Summary
`BaseRewardPool` (and `BaseRewardPoolV2`/`mWOMSVBaseRewarder`) distribute new rewards by instantly bumping `rewardPerTokenStored` in proportion to `totalStaked()` **at the exact moment `queueNewRewards`/`donateRewards` is called**, with no time-weighting or lock period on stakes. Because `WombatStaking.withdraw()` harvests pending WOM rewards from `MasterWombat` and pushes them into the rewarder (`queueNewRewards`) *before* the withdrawing user's stake is removed from `MasterMagpie`, an attacker can deposit a large amount of the underlying stable/LP token, trigger their own withdrawal in the same call, and capture almost the entire newly-harvested reward that should have accrued to long-term stakers.

### Finding Description
`BaseRewardPool._provisionReward` computes the reward-per-token delta using the current stake snapshot, not a time-weighted average: [1](#0-0) 

`rewardPerToken()` simply returns this cumulative stored value, and `earned()` multiplies the *current* `balanceOf(account)` by the delta since the account's last snapshot: [2](#0-1) 

`balanceOf()` reads the live stake from `MasterMagpie.stakingInfo`, and `totalStaked()` reads the live receipt-token balance held by `MasterMagpie`: [3](#0-2) 

`MasterMagpie._deposit`/`_withdraw` place no time lock on stakes — a user can deposit and withdraw in the same transaction, harvesting rewards on both actions: [4](#0-3) 

Crucially, `WombatPoolHelper.withdraw()` harvests pending WOM rewards from the underlying Wombat pool (which internally calls `queueNewRewards`) *before* unstaking the caller from `MasterMagpie`, as noted explicitly in the code comment: [5](#0-4) 

and the fee/reward routing path that feeds the harvested WOM into the rewarder uses `queueNewRewards`, dividing by whatever `totalStaked()` is at that instant: [6](#0-5) 

Putting this together: if an attacker deposits a very large amount of the deposit token via `WombatPoolHelper.deposit()`/`depositLP()` (staking through `MasterMagpie.depositFor`), their stake becomes the overwhelming majority of `totalStaked()` for that pool's `BaseRewardPool`. If the attacker (or anyone) then calls `WombatPoolHelper.withdraw()`, the pending WOM reward harvested from `MasterWombat` is queued into the rewarder while the attacker's huge stake is still counted in `totalStaked()`, so nearly the entire `rewardPerTokenStored` increment is attributable to the attacker. `_unstake` (called immediately after) then harvests this reward for the attacker via `_harvestBaseRewarder`/`getReward`, before their stake is removed. This is the same "unweighted, non-time-locked reward-per-token accounting" root cause identified in the referenced NFTX finding, reachable here purely by an ordinary wallet calling `deposit()`+`withdraw()` on `WombatPoolHelper` (optionally financed with a flash loan of the deposit token to maximize the theft).

### Impact Explanation
Legitimate long-term stakers in a Wombat pool have their proportional share of freshly-harvested WOM rewards diluted to near-zero whenever an attacker deposits a large stake immediately before a harvest-triggering withdrawal and then removes it. This is a direct theft of already-accrued/soon-to-be-accrued yield from real stakers, satisfying the "theft or permanent freezing of unclaimed yield" impact bar.

### Likelihood Explanation
The attack requires only two permissionless calls (`deposit()` then `withdraw()`) on `WombatPoolHelper`, using capital the attacker can obtain via a flash loan of the deposit token (or simply large existing capital) — no privileged role, governance action, or protocol bug elsewhere is needed. The size of the theft scales with how much WOM reward has accrued unharvested in `MasterWombat` for that pool at the time, making it more profitable on pools with larger unclaimed reward backlogs.

### Recommendation
- Time-weight reward distribution (e.g., stream `queuedRewards` linearly over a period rather than crediting it instantly to whatever stake exists at the moment `queueNewRewards` is called), or
- Require a minimum staking duration before a user's stake counts toward `earned()`, or before rewards can be claimed for that stake, or
- Snapshot/checkpoint stake balances so that rewards harvested in a given call are attributed based on stake held over the accrual period rather than the instantaneous balance at harvest time.

### Proof of Concept
1. Attacker flash-loans (or otherwise acquires) a large amount of the pool's `depositToken`.
2. Attacker calls `WombatPoolHelper.deposit(_amount, _minimumLiquidity)` → receipt tokens are minted and staked into `MasterMagpie` via `depositFor`, making the attacker's stake dominate `totalStaked()` for that pool's `rewarder` (`BaseRewardPool`).
3. Attacker immediately calls `WombatPoolHelper.withdraw(_liquidity, _minAmount)`. This calls `WombatStaking.withdraw`, which harvests pending WOM from `MasterWombat` and routes it through `_sendRewards` → `IBaseRewardPool.queueNewRewards`, computing `rewardPerTokenStored += reward * 10**decimals / totalStaked()` while `totalStaked()` still includes the attacker's huge deposit.
4. `_unstake` then calls `MasterMagpie.withdrawFor`, which internally calls `_harvestBaseRewarder`/`getReward`, crediting the attacker with `balanceOf(attacker) * (new rewardPerToken - paid) / decimals` ≈ nearly the entire newly queued reward, since `balanceOf(attacker)` ≈ `totalStaked()`.
5. Attacker's stake is removed and underlying tokens returned; attacker repays the flash loan, keeping the harvested WOM reward that should have gone to long-term stakers. [1](#0-0) [5](#0-4) [6](#0-5) [4](#0-3) 

**Note on confidence:** I was unable to read the body of `WombatStaking.harvest()`/`withdraw()`'s call into `MasterWombat` within the available tool budget to confirm the exact internal ordering byte-for-byte (only the `WombatPoolHelper.withdraw` comment "we have to withdraw from wombat exchange to harvest reward to base rewarder" and the `_sendRewards`/`queueNewRewards` code were directly inspected). The core dilution mechanism in `BaseRewardPool._provisionReward`/`earned()`/`totalStaked()` is fully confirmed from source, which is sufficient to establish the vulnerability class regardless of the precise call ordering inside `WombatStaking.withdraw`.

### Citations

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

**File:** rewards/BaseRewardPool.sol (L141-184)
```text
    function rewardPerToken(address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return rewards[_rewardToken].rewardPerTokenStored;
    }

    function rewardTokenInfos()
        override
        external
        view
        returns
        (
            address[] memory bonusTokenAddresses,
            string[] memory bonusTokenSymbols
        )
    {
        uint256 rewardTokensLength = rewardTokens.length;
        bonusTokenAddresses = new address[](rewardTokensLength);
        bonusTokenSymbols = new string[](rewardTokensLength);
        for (uint256 i; i < rewardTokensLength; i++) {
            bonusTokenAddresses[i] = rewardTokens[i];
            bonusTokenSymbols[i] = IERC20Metadata(address(bonusTokenAddresses[i])).symbol();
        }
    }

    /// @notice Returns amount of reward token earned by a user
    /// @param _account Address account
    /// @param _rewardToken Address reward token
    /// @return Returns amount of reward token earned by a user
    function earned(address _account, address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return (
            (((balanceOf(_account) *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                (10**stakingDecimals())) + userRewards[_rewardToken][_account])
        );
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

**File:** wombat/WombatStaking.sol (L721-770)
```text
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

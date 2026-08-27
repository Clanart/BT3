### Title
Reward Theft via Flashloaned Deposit Sniping Around Reward Distribution - (File: rewards/BaseRewardPool.sol)

### Summary
`BaseRewardPool` computes `rewardPerTokenStored` at the moment new rewards are provisioned by dividing the incoming reward amount by `totalStaked()`, which is the *live* balance of `stakingToken` held by `MasterMagpie` [1](#0-0) . Because `MasterMagpie.deposit`/`depositFor` and `withdraw`/`withdrawFor` have no cooldown, vesting, or minimum holding period [2](#0-1) , and reward distribution can be triggered by any wallet (via the permissionless `donateRewards` or via the permissionless `WombatPoolHelper.harvest()` → `WombatStaking._toMasterWomAndSendReward` → `queueNewRewards` path) [3](#0-2) [4](#0-3) [5](#0-4) , an attacker can inflate their staked share immediately before a reward is queued and withdraw immediately after, capturing a share of rewards proportional to a flash-sized deposit rather than to actual time-at-risk. This is directly analogous to the CompounderFinance bug class: exploitation of instantaneous fluctuations in the amount of "exchangeable"/staked assets to mint a disproportionate claim on value (there, share price; here, `rewardPerTokenStored`/`earned()`).

### Finding Description
`earned()` computes a user's claim strictly from the current `rewardPerTokenStored` snapshot vs. the user's `userRewardPerTokenPaid` snapshot, multiplied by their *current* `balanceOf()` [6](#0-5) . `balanceOf()` reads the live `userInfo[stakingToken][account].amount` from `MasterMagpie.stakingInfo` [7](#0-6) , which is updated instantly and atomically on `deposit`/`withdraw` with no delay [2](#0-1) .

Because there is no minimum staking duration, snapshot-per-block, or fee-on-entry/exit mechanism, a wallet can, within a short window (or even a single transaction if reward tokens support flash-loanable liquidity):
1. Deposit a very large amount of `stakingToken` (e.g., wombat pool receipt tokens or `mWOM`) into `MasterMagpie` right before a reward distribution event.
2. Trigger (or wait for) `queueNewRewards`/`donateRewards` to fire, which divides the new reward pool by the now-inflated `totalStaked()`, immediately crediting the attacker's inflated `balanceOf()` a large slice of `rewardPerTokenStored` the instant it updates via `_updateFor` (called through `updateReward`/`updateRewards` on the next state-changing call) [8](#0-7) .
3. Immediately claim/harvest and withdraw, since `withdraw`/`withdrawFor` triggers `_harvestBaseRewarder` before decrementing the user's stake [9](#0-8) .

The attacker pays no cost proportional to time-at-risk (unlike honest long-term stakers who bore the risk/opportunity cost of holding), and the reward that would otherwise have accrued proportionally to genuine long-term stakers is redirected to the flash staker. `donateRewards` is fully permissionless and callable by any wallet with the reward token, so the attacker (or an accomplice) can directly control the timing of the reward injection to synchronize with their inflated deposit [10](#0-9) .

### Impact Explanation
This results in theft of unclaimed yield belonging to legitimate long-term stakers in any `MasterMagpie` pool (WombatStaking receipt-token pools, `mWOM`, `vlMGP`, etc.), since the attacker extracts a disproportionate share of newly queued rewards using capital held for a negligible duration, diluting/stealing the yield legitimate stakers would have earned from that same distribution. This matches the "theft of unclaimed yield" impact category.

### Likelihood Explanation
High: any unprivileged wallet holding the relevant `stakingToken` (or the ability to acquire/flash-borrow it) can execute the deposit → (trigger or await) reward injection → withdraw sequence with no special privileges. The `donateRewards` entrypoint is explicitly public and unguarded aside from requiring the token to already be registered as a reward token, making the timing of the exploit fully attacker-controlled rather than dependent on waiting for protocol-driven harvests.

### Recommendation
Introduce a checkpoint/snapshot mechanism so `rewardPerTokenStored` updates account for stake duration (e.g., a time-weighted or block-based accrual model as in standard Synotehtix-style `rewardPerToken()` computed continuously rather than only at provisioning time), and/or add a minimum staking duration or withdrawal cooldown before a deposit becomes eligible for newly queued rewards. Alternatively, make `totalStaked()`/reward eligibility based on a stake snapshot taken prior to `queueNewRewards`/`donateRewards` execution rather than the instantaneous balance at provisioning time.

### Proof of Concept
1. Attacker monitors mempool/pending state for an upcoming `WombatPoolHelper.harvest()` call (permissionless) or prepares to call `BaseRewardPool.donateRewards()` directly.
2. Attacker calls `WombatPoolHelperV2.deposit()`/`depositFor()` with a very large amount of `depositToken`, which mints receipt tokens and stakes them via `MasterMagpie.depositFor` [11](#0-10) , inflating `IERC20(stakingToken).balanceOf(MasterMagpie)`.
3. Attacker calls `BaseRewardPool.donateRewards(rewardAmount, rewardToken)` (or the harvest fires), causing `_provisionReward` to compute `rewardPerTokenStored += (rewardAmount * 10**decimals) / totalStaked()` using the now-inflated `totalStaked()` [12](#0-11) .
4. Attacker immediately calls `WombatPoolHelperV2.withdraw()`, which internally triggers `_harvestBaseRewarder` before unstaking, crediting the attacker `balanceOf(attacker) * (rewardPerTokenStored - userRewardPerTokenPaid)` — a share proportional to their flash-sized deposit rather than genuine time held [13](#0-12) .
5. Attacker claims the reward and exits, having captured yield diluted away from legitimate long-term stakers with only momentary capital exposure.

### Citations

**File:** rewards/BaseRewardPool.sol (L130-136)
```text
    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/BaseRewardPool.sol (L169-185)
```text
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

**File:** rewards/BaseRewardPool.sol (L288-295)
```text
    function _updateFor(address _account) internal {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            userRewards[rewardToken][_account] = earned(_account, rewardToken);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
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

**File:** rewards/MasterMagpie.sol (L481-514)
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

**File:** wombat/WombatPoolHelper.sol (L142-144)
```text
    function harvest() external override {
        IWombatStaking(wombatStaking).harvest(lpToken);
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

**File:** wombat/WombatPoolHelperV2.sol (L155-162)
```text
    function _deposit(uint256 _amount, uint256 _minimumLiquidity, address _for, address _from) internal {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, _for, _from);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, _for);
        
        emit NewDeposit(_for, _amount);
    }
```

No lock time or cooldown exists on deposit/withdraw, confirming the attack is atomic and executable in a single transaction.

### Title
First-staker capture of the queued reward backlog via `donateRewards` - (File: rewards/BaseRewardPool.sol)

### Summary
`_provisionReward` (invoked by the unprivileged `donateRewards`) routes all reward provisions into `rewards[_rewardToken].queuedRewards` whenever `totalStaked() == 0`, and on the next provision made while `totalStaked() > 0` it dumps the *entire* accumulated backlog into `rewardPerTokenStored` in one jump, divided only by whatever stake exists at that instant. [1](#0-0)  Because `MasterMagpie.deposit`/`withdraw` have no lock time or cooldown, an attacker can atomically become the sole staker, trigger the flush, and withdraw with the harvested reward, capturing a backlog that accrued entirely before they ever held a position. [2](#0-1) 

### Finding Description
`donateRewards` is a fully permissionless entrypoint that only requires the token to already be a registered reward token; it calls `_provisionReward` with no access control. [3](#0-2) 

Inside `_provisionReward`:
- While `this.totalStaked() == 0`, every donated/queued amount is only added to `rewardInfo.queuedRewards`, and `rewardPerTokenStored` is left untouched. [4](#0-3) 
- The very next call to `_provisionReward` (via `donateRewards` or `queueNewRewards`) that occurs while `totalStaked() > 0` folds `queuedRewards` into the current `_amountReward` and computes a single `rewardPerTokenStored` jump using `totalStaked()` **at that instant**: `rewardPerTokenStored += (_amountReward * 10**stakingDecimals) / totalStaked()`. [5](#0-4) 

`totalStaked()` simply reads `IERC20(stakingToken).balanceOf(operator)`, i.e., the live balance in `MasterMagpie` at call time — there is no snapshotting of "who was staked during the accrual window". [6](#0-5) 

`earned()`/`userRewardPerTokenPaid` are keyed off `rewardPerToken()` deltas since the account's last checkpoint, and a freshly-staked account's checkpoint (`userRewardPerTokenPaid`) is set to the pre-jump value, so it captures 100% of the jump if it is the only staker present when the jump happens. [7](#0-6) 

Exploit flow (single transaction, e.g. from a flash-loan-funded contract):
1. Ensure/observe `totalStaked() == 0` for the pool (all prior stakers exited, or a fresh pool with pre-accrued `queuedRewards` from earlier `donateRewards`/`queueNewRewards` calls made while empty).
2. Flash-loan the staking token, call `MasterMagpie.deposit(stakingToken, amount)` — even a tiny amount — becoming the sole staker. [8](#0-7) 
3. Call `BaseRewardPool.donateRewards(1, rewardToken)` (or any nonzero amount) — this is permissionless and only needs 1 wei of the already-registered reward token. This forces `_provisionReward` into the `totalStaked() > 0` branch, flushing the entire `queuedRewards` backlog into `rewardPerTokenStored` divided by the attacker's own (sole) stake share = 100%. [5](#0-4) 
4. Call `MasterMagpie.withdraw(stakingToken, amount)`, which internally calls `_harvestBaseRewarder` → `getReward`, crediting the attacker with the full flushed reward via `earned()`. [9](#0-8) 
5. Repay the flash loan; net result: attacker walks away with the reward backlog that accrued while they held no stake.

`MasterMagpie.deposit`/`withdraw` carry no time-lock, cooldown, or minimum staking-duration check (only `whenNotPaused`/`nonReentrant`), and each internal call is a separate top-level call so the guard does not block this atomic sequence. [10](#0-9) 

### Impact Explanation
This is direct theft of unclaimed yield belonging to future/other stakers: a backlog of reward tokens accrued while the pool was empty (potentially from many `donateRewards`/`queueNewRewards` calls over time) is fully redirected to a single one-block staker instead of being fairly distributed pro-rata over the time it should accrue to actual long-term stakers. This matches "theft of unclaimed yield" and can be executed with capital cost approaching zero via flash loans, satisfying a Critical severity direct-theft class.

### Likelihood Explanation
Feasibility is high: `donateRewards` is unprivileged and callable by anyone with 1 wei of the reward token; `MasterMagpie.deposit`/`withdraw` are unprivileged and have no lock/cooldown; the whole sequence is atomic within one transaction and repeatable any time `totalStaked()` drops to zero (e.g., pool churn, migration, low-TVL pools, or newly created pools before the first “real” staker arrives) with pre-existing `queuedRewards`. No special role is required.

### Recommendation
Do not let the reward-per-token index absorb a static backlog based on the instantaneous stake at the time of the next provision. Options: (1) accrue rewards over time using a rate-based streaming model (reward-per-second distributed over a duration) rather than an instantaneous index jump, so a one-block staker cannot capture a multi-block backlog in an instant; (2) when `totalStaked() == 0`, refuse to accept/queue donations at all (revert or hold in an escrow not tied to `rewardPerTokenStored`) until deposits exist for a minimum duration; (3) require a minimum bonding/lock period after `deposit` before a user's stake counts toward `rewardPerToken` distribution eligibility, preventing same-block deposit-harvest-withdraw sequences.

### Proof of Concept
Foundry/Hardhat test plan:
1. Deploy `MasterMagpie`, `BaseRewardPool` (staking token `LP`, reward token `RWD`), register `RWD` as reward token.
2. Ensure `totalStaked() == 0` (no deposits yet).
3. Manager or any user calls `queueNewRewards`/`donateRewards(100e18, RWD)` several times → confirm `rewards[RWD].queuedRewards == 100e18 * N`, `rewardPerTokenStored == 0`.
4. Attacker contract, in a single transaction:
   - Flash-loan `1 LP` (or borrow from a mock lender), `approve` and `MasterMagpie.deposit(LP, 1)`.
   - Call `donateRewards(1, RWD)`.
   - Assert `rewards[RWD].queuedRewards == 0` and `rewards[RWD].rewardPerTokenStored` jumped by `(backlog+1) * 1e18 / 1`.
   - Call `MasterMagpie.withdraw(LP, 1)` and assert attacker received (approximately) the entire backlog in `RWD`.
   - Repay flash loan.
5. Differential check: run the same total `donateRewards` amount but split across multiple stakers holding stake proportionally over time (i.e., simulate stake existing throughout the accrual period) — assert the per-account harvested reward differs sharply between the "attacker sole staker in one block" run and the "distributed among legitimate long-term stakers" run, proving the backlog is misappropriated to the flash attacker instead of being reconciled with actual stake-time.

### Citations

**File:** rewards/BaseRewardPool.sol (L126-128)
```text
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }
```

**File:** rewards/BaseRewardPool.sol (L141-185)
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
    }
```

**File:** rewards/BaseRewardPool.sol (L279-284)
```text
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

### Title
First-depositor front-run of reward seeding steals entire donated/queued reward via `_provisionReward` division-by-tiny-totalStaked - (File: rewards/BaseRewardPool.sol / rewards/BaseRewardPoolV2.sol)

### Summary
`BaseRewardPool`/`BaseRewardPoolV2` guard reward accounting only against a **literal zero** `totalStaked()`, not against a **near-zero** value. Because `totalStaked()` is the raw token balance held by `MasterMagpie` (not a virtual-share count), any unprivileged wallet can become the sole staker of a freshly created or freshly-emptied pool by depositing 1 wei, then capture 100% of any reward subsequently pushed into the pool via the fully permissionless `donateRewards` (or the manager-only `queueNewRewards`, whose effect on a 1-wei-total pool is identical).

### Finding Description
`totalStaked()` in `BaseRewardPool.sol` returns the actual staking-token balance of the `operator` (MasterMagpie), which is directly controllable by depositing the smallest possible unit: [1](#0-0) .

`MasterMagpie.deposit()` is a fully public, unprivileged entry point that lets anyone become the pool's sole depositor with an arbitrarily small amount: [2](#0-1) , feeding into `_deposit` with no minimum-deposit enforcement: [3](#0-2) .

Reward accounting in `_provisionReward` only special-cases `totalStaked() == 0`, otherwise it directly divides the incoming reward amount by `totalStaked()` to update `rewardPerTokenStored`: [4](#0-3) . The identical pattern exists in `BaseRewardPoolV2._provisionReward`: [5](#0-4) .

Critically, `donateRewards` — which calls `_provisionReward` — carries **no access control at all**, so any wallet (not just the manager) can trigger this division: [6](#0-5) . `queueNewRewards` (manager-only, but triggered as part of normal protocol operation such as harvest flows) hits the exact same math: [7](#0-6) .

This is the same root cause as the referenced Beedle `Staking.sol` bug: the reward index is derived from `newRewards / totalStaked`, and when `totalStaked` is 1 wei (instead of literally 0), the "zero-supply" guard does not help — the resulting `rewardPerTokenStored` becomes astronomically large and is entirely attributable to the tiny staker. Once that staker calls `getReward`, `earned()` computes `balanceOf(_account) * (rewardPerToken - paid) / 10**decimals`, and because the staker's `balanceOf` is the (near) totality of `totalStaked` at that moment, they receive (almost) the entire reward that was intended to be shared with future/legitimate stakers: [8](#0-7) , [9](#0-8) .

### Impact Explanation
An attacker can steal newly seeded/queued reward tokens intended for a pool's staker base by ensuring they are the sole (or overwhelmingly dominant) staker at the moment rewards are provisioned. This is a direct theft of reward-token yield belonging to the protocol/other stakers, satisfying "theft or permanent freezing of unclaimed yield." The attack requires no privileged role — only ordinary `deposit`/`donateRewards`/`getReward` calls available to any wallet.

### Likelihood Explanation
The attack window exists at pool creation (before other depositors arrive) and, more generally, any time `totalStaked()` drops to a very small value (e.g., after most stakers withdraw). Since `donateRewards` is public with no minimum, an attacker can even self-trigger provisioning after front-running with 1 wei, but the realistic theft scenario is capturing rewards from a legitimate `queueNewRewards`/`donateRewards` call made by the protocol team or another user shortly after pool deployment — a routine and expected operational sequence, making the precondition (attacker being first/near-sole depositor) easily and cheaply achievable with mempool monitoring or simply being early.

### Recommendation
Add a minimum-liquidity / virtual-shares safeguard in `_provisionReward` (e.g., require `totalStaked() >= MINIMUM_STAKE` before applying the reward-per-token update, otherwise queue it, similar to Uniswap V2's dead-shares pattern), and/or require pools to receive a locked, non-withdrawable seed deposit before enabling reward distribution. Restrict `donateRewards` so it cannot be abused to force a premature, disproportionate index update while `totalStaked()` is negligible.

### Proof of Concept
1. `MasterMagpie` owner registers a new pool for staking token `X` with rewarder `R` (a `BaseRewardPool`). At this point `totalStaked() == 0`.
2. Attacker calls `MasterMagpie.deposit(X, 1)` — 1 wei of `X` — making themselves the sole staker; `totalStaked() == 1`.
3. Shortly after, the protocol (or any wallet) calls `R.donateRewards(1_000e18, rewardToken)` (or the normal harvest flow triggers `queueNewRewards`) to seed pool incentives. Since `totalStaked() != 0`, `_provisionReward` computes `rewardPerTokenStored += 1_000e18 * 10**decimals / 1`, an enormous index jump.
4. Attacker calls `getReward` on `R` via `MasterMagpie`, receiving essentially the entire 1,000 tokens of reward, `earned()` being dominated by their near-100% share of `totalStaked()` at the time of the update.
5. Later legitimate depositors receive none of that donated reward, as it has already been extracted.

### Citations

**File:** rewards/BaseRewardPool.sol (L124-128)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
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

**File:** rewards/BaseRewardPool.sol (L219-240)
```text
    /// @notice Calculates and sends reward to user. Only callable by masterMagpie
    /// @param _account Address account
    function getReward(address _account, address _receiver)
        override
        public
        onlyMasterMagpie
        updateReward(_account)
        returns (bool)
    {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            uint256 reward = userRewards[rewardToken][_account]; // updated during updateReward modifier
            if (reward > 0) {
                userRewards[rewardToken][_account] = 0;
                IERC20(rewardToken).safeTransfer(_receiver, reward);
                emit RewardPaid(_account, _receiver, reward, rewardToken);
            }
        }

        return true;
    }
```

**File:** rewards/BaseRewardPool.sol (L258-274)
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

**File:** rewards/MasterMagpie.sol (L334-339)
```text
    /// @notice Deposits staking token to the pool, updates pool and distributes rewards
    /// @param _stakingToken Staking token of the pool
    /// @param _amount Amount to deposit to the pool
    function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _deposit(_stakingToken, msg.sender, _amount, false);
    }
```

**File:** rewards/MasterMagpie.sol (L481-505)
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
```

**File:** rewards/BaseRewardPoolV2.sol (L290-312)
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
```

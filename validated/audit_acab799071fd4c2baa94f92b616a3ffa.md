### Title
Direct ERC20 donation to MasterMagpie inflates `BaseRewardPool.totalStaked()`, permanently diluting/freezing distributed reward tokens - (File: rewards/BaseRewardPool.sol)

### Summary
`BaseRewardPool.totalStaked()` reads the raw `IERC20(stakingToken).balanceOf(operator)` instead of a separately tracked accounting variable, while `MasterMagpie` only credits a user's stake in `userInfo[_stakingToken][_account].amount/available` when tokens pass through `deposit()`/`depositFor()`. An attacker can transfer staking tokens directly to `MasterMagpie` (the `operator`) without calling `deposit()`, inflating `totalStaked()` right before a manager calls `queueNewRewards()`, causing `rewardPerTokenStored` to be permanently under-credited relative to the reward tokens actually pulled into the pool.

### Finding Description
`totalStaked()` in `rewards/BaseRewardPool.sol` (and identically in `BaseRewardPoolV2.sol`) is: [1](#0-0) 

is used as the denominator in `_provisionReward`: [2](#0-1) 

Meanwhile, `MasterMagpie._deposit` only increments a user's tracked balance (`user.amount`, `user.available`, used by `BaseRewardPool.balanceOf` via `stakingInfo`) when the ERC20 transfer flows through the `deposit`/`depositFor` code path: [3](#0-2) 

and `stakingInfo` (used by `BaseRewardPool.balanceOf`) only reflects `userInfo`, not raw contract balance: [4](#0-3) 

Because `IERC20.transfer()` is a permissionless call that any token holder can invoke against `MasterMagpie`'s address, an attacker can:
1. Watch the mempool (or wait for a predictable/scheduled) `queueNewRewards(_amountReward, _rewardToken)` call from the reward manager.
2. Front-run it with a plain `stakingToken.transfer(masterMagpie, X)`, which raises `IERC20(stakingToken).balanceOf(operator)` without incrementing any `userInfo[stakingToken][attacker]` entry.
3. The manager's `queueNewRewards` executes `_provisionReward`, computing `rewardPerTokenStored += (_amountReward * 10**stakingDecimals()) / totalStaked()` using the inflated `totalStaked()`.
4. The full `_amountReward` reward tokens are pulled into the `BaseRewardPool` via `safeTransferFrom`, but `rewardPerTokenStored` only increases proportionally to the artificially large (donated) denominator, so a portion of the reward tokens are never attributable to any staker's `earned()` calculation and become permanently stuck in the reward pool contract.
5. The attacker cannot recover the donated staking tokens afterward through `MasterMagpie.withdraw()`, since `_withdraw`/`_harvestAndUnstake` checks `user.available < _amount` against the tracked `userInfo`, which was never incremented by the raw transfer — so this is not a self-serving withdrawal exploit, but a pure griefing/permanent-freeze of the queued reward tokens for legitimate stakers.

No existing modifier (`onlyManager` on `queueNewRewards`, `nonReentrant`/`whenNotPaused` on deposit/withdraw) prevents an unrelated ERC20 transfer directly to the operator address, since `totalStaked()` has no dependency on `deposit()` having been called.

### Impact Explanation
This results in permanent freezing of a portion of newly-queued reward tokens: the reward tokens are transferred into the `BaseRewardPool` contract (real ERC20 balance increases by `_amountReward`) but `rewardPerTokenStored` is diluted below the value it should be for the actually-staked supply, so the shortfall is never claimable by any user and is trapped forever in the contract. This matches the "theft or permanent freezing of unclaimed yield" impact class.

### Likelihood Explanation
The precondition is minimal: the attacker needs to hold/acquire the relevant staking token and successfully front-run (or simply time) a `queueNewRewards` call, which is a routine, often externally-visible/predictable operation performed by a `rewardManager`. No special privileges are required, and the attack is repeatable every time rewards are queued. The magnitude of the dilution scales with the ratio of donated amount to legitimate `totalStaked()`, so it is most impactful on pools with low real deposits, but is possible on any pool.

### Recommendation
Replace `totalStaked()`'s reliance on raw `IERC20(stakingToken).balanceOf(operator)` with an explicitly tracked accounting variable (e.g., a running total updated in `MasterMagpie._deposit`/`_withdraw`, or a dedicated `totalStaked` mapping incremented/decremented alongside `userInfo[...].amount`), so that unsolicited direct transfers to the operator cannot influence reward-per-token calculations in `_provisionReward`.

### Proof of Concept
Foundry test plan:
1. Deploy `MasterMagpie`, a staking ERC20 mock, and its `BaseRewardPoolV2` (or `BaseRewardPool`) rewarder, register the pool via `add()`.
2. User A deposits `1000e18` staking tokens via `MasterMagpie.deposit()`.
3. Record `rewardPerTokenBefore = rewarder.rewardPerToken(rewardToken)` and `totalStakedBefore = rewarder.totalStaked()` (== 1000e18).
4. Attacker (not a staker) calls `stakingToken.transfer(address(masterMagpie), 9000e18)` directly (bypassing `deposit`).
5. Assert `rewarder.totalStaked() == 10000e18` while `rewarder.balanceOf(attacker) == 0` and `stakingInfo(stakingToken, attacker).stakedAmount == 0`.
6. Reward manager calls `queueNewRewards(1000e18, rewardToken)`.
7. Compute expected `rewardPerTokenStored` if `totalStaked()` had remained `1000e18` (i.e., `1000e18 * 10**decimals / 1000e18`) vs. the actual on-chain value using the inflated `10000e18` denominator; assert the actual value is ~10x lower than the "honest" expectation.
8. Assert User A's `earned(userA, rewardToken)` is far below `1000e18` (the amount that was pulled into the contract), and that `IERC20(rewardToken).balanceOf(address(rewarder))` still holds the undistributed remainder with no code path to reclaim it for stakers.
9. Attempt `MasterMagpie.withdraw(stakingToken, 9000e18)` from attacker's account and assert it reverts with `WithdrawAmountExceedsStaked`, confirming the donated tokens (and the diluted reward share they caused) are permanently stuck/frozen.

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

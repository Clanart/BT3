### Title
Fee-on-transfer reward tokens break reward accounting in permissionless `donateRewards()` — ([File: rewards/BaseRewardPool.sol])

### Summary
`BaseRewardPool.donateRewards()` / `BaseRewardPoolV2.donateRewards()` (and their `_provisionReward` internals) are permissionless, callable by any ordinary wallet, and credit `rewardPerTokenStored` based on the caller-supplied `_amountReward` parameter instead of the actual amount received by the contract. For a reward token that charges a transfer fee, the pool's promised reward balance becomes larger than the tokens it actually holds, ultimately causing some stakers to be unable to claim already-accrued rewards.

### Finding Description
`_provisionReward` performs `safeTransferFrom(msg.sender, address(this), _amountReward)` and then unconditionally uses `_amountReward` (not the measured balance delta) to update `rewardInfo.historicalRewards` and `rewardInfo.rewardPerTokenStored`: [1](#0-0) 

`donateRewards` is external and unrestricted (only requires the token to already be a registered reward token): [2](#0-1) 

The same pattern exists in `BaseRewardPoolV2._provisionReward`: [3](#0-2) 

`getReward()` later pays users out of the contract's real token balance using a plain `safeTransfer` based on `rewardPerTokenStored`-derived accrued amounts: [4](#0-3) 

If `_rewardToken` is a fee-on-transfer token, the pool receives less than `_amountReward`, yet `rewardPerTokenStored` is incremented as if the full amount arrived. This inflates every staker's `earned()` entitlement beyond what the pool actually holds for that token.

### Impact Explanation
Because entitlements (`rewardPerTokenStored`) exceed the real token balance held by the pool, the reward pool becomes under-collateralized for that token. Once enough stakers claim, the contract will run out of the shortfalled reward token balance, and the remaining stakers' `getReward()` calls will revert on `safeTransfer` (insufficient balance) for that token, permanently freezing their already-accrued, legitimately-earned yield. This is a direct insolvency/freezing-of-yield outcome caused entirely by unprivileged interaction with `donateRewards`, requiring no admin or governance action.

### Likelihood Explanation
`donateRewards` is callable by anyone with no access control beyond the token already being registered as a reward token (`isRewardToken[_rewardToken]`). Reward tokens are not restricted to a small whitelist enforced at the reward-pool layer itself — any address holding the registered token can trigger the mis-accounting simply by calling `donateRewards` with a fee-on-transfer token. This requires only one transaction from an ordinary wallet and no cooperation from privileged roles, making it straightforward for any user (or attacker) to trigger the accounting corruption as soon as any deployed reward token in the ecosystem has (or later switches to) a transfer fee.

### Recommendation
In `_provisionReward`, measure the actual amount received via a balance-before/balance-after check (as is already done correctly elsewhere in the codebase, e.g. `WombatPoolHelper.depositLP`/`WombatStaking.deposit`), and use that measured delta — not the caller-supplied `_amountReward` — when updating `historicalRewards` and `rewardPerTokenStored`.

### Proof of Concept
1. A reward token `T` is registered as a valid reward token for some `BaseRewardPool` (via `queueNewRewards` from a manager, e.g. WombatStaking/MasterMagpie flow) — this happens once during normal protocol operation for tokens such as bribe reward tokens.
2. `T` later becomes (or already is) a fee-on-transfer token (e.g. 5% fee on transfer), or an attacker registers/uses a token with such behavior where allowed.
3. Any wallet calls `pool.donateRewards(1000e18, T)`.
4. `_provisionReward` executes `safeTransferFrom(msg.sender, pool, 1000e18)`, but due to the 5% fee only 950e18 actually lands in the pool.
5. `rewardInfo.rewardPerTokenStored` is nonetheless incremented using the full `1000e18`, so `earned()` for all current stakers now sums to 1000e18 worth of `T`, while the pool only holds 950e18.
6. Stakers begin calling `getReward()`; the first claimants succeed, but the last claimants' `safeTransfer(rewardToken, ...)` calls in `getReward()` revert due to insufficient contract balance, permanently freezing their earned reward for token `T`. [1](#0-0) [5](#0-4)

### Citations

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

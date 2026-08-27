### Title
Permanent stuck reward dust due to integer division truncation in reward-per-token accounting - (File: rewards/BaseRewardPool.sol)

### Summary
`BaseRewardPool`, `BaseRewardPoolV2`, `vlMGPBaseRewarder`, and `mWOMSVBaseRewarder` all use the same Synthetix-style reward accounting pattern where `rewardPerTokenStored` is incremented by `(amountReward * 10**decimals) / totalStaked()` every time rewards are queued/donated. This integer division truncates a remainder on every call, and that remainder is transferred into the contract's balance but never reflected in `rewardPerTokenStored`, so it can never be attributed to or claimed by any staker, permanently accumulating as stuck, unclaimable yield — the same root cause as the referenced Curves `FeeSplitter.addFees` finding.

### Finding Description
`_provisionReward` in `BaseRewardPool.sol` pulls in `_amountReward` of the reward token via `safeTransferFrom` and then updates the global accumulator: [1](#0-0) 

```
rewardInfo.rewardPerTokenStored =
    rewardInfo.rewardPerTokenStored +
    (_amountReward * 10**stakingDecimals()) /
    this.totalStaked();
```

Because Solidity integer division floors the result, any remainder of `(_amountReward * 10**stakingDecimals()) % totalStaked()` is silently dropped from `rewardPerTokenStored` even though the full `_amountReward` was already transferred into the contract and counted in `historicalRewards`. Per-user claimable amounts are computed purely from `rewardPerTokenStored`: [2](#0-1) 

Since `earned()`/`_earned()` only ever reference `rewardPerToken(_rewardToken)` (i.e., `rewardPerTokenStored`), the truncated remainder can never be assigned to any account's `userRewards`. This happens on every single `queueNewRewards`/`donateRewards` call across the lifetime of each pool, so the stuck amount monotonically grows and is never reduced. None of these reward-pool contracts (`BaseRewardPool.sol`, `BaseRewardPoolV2.sol`, `vlMGPBaseRewarder.sol`, `mWOMSVBaseRewarder.sol`) expose a rescue/sweep function to recover such dust — a repo-wide search for `rescue`/`withdrawERC20`/`sweep` only turns up unrelated matches in `wombat/WomUp.sol`, confirming there is no path to reclaim these funds from the affected reward pools.

The identical pattern is repeated in `_provisionReward`/`_queueNewRewardsWithoutTransfer` of `BaseRewardPoolV2.sol`, `vlMGPBaseRewarder.sol`, and `mWOMSVBaseRewarder.sol`: [3](#0-2) [4](#0-3) [5](#0-4) 

### Impact Explanation
This is a genuine analog of the Curves `FeeSplitter` bug class: an ordinary unprivileged wallet interacts with `MasterMagpie`/staking flows (`deposit`, `withdraw`, `getReward`) that trigger routine reward distribution through these `BaseRewardPool`-family contracts. Each distribution cycle permanently locks a small remainder of already-transferred reward tokens in the contract with no accounting path or recovery mechanism to ever distribute or withdraw it. Over the lifetime of a pool (many reward-queuing events across many reward tokens and pools), this results in a permanently frozen and growing pool of unclaimed yield that is unrecoverable by any user or admin action, satisfying "permanent freezing of funds / unclaimed yield."

### Likelihood Explanation
Reward provisioning (`queueNewRewards`/`donateRewards`) is a routine, high-frequency operation in the protocol's normal reward-harvesting flow, not a rare edge case, so the truncation occurs on essentially every reward distribution for every pool and every reward token, guaranteeing the stuck balance to accrue continuously and indefinitely.

### Recommendation
Track a `remainder`/dust value in the `Reward` struct and carry it forward into the next `_provisionReward` call (add it to `_amountReward` before recomputing `rewardPerTokenStored`), similar to how `queuedRewards` is already carried forward when `totalStaked() == 0`. Alternatively, add an owner/manager-gated sweep function that can redistribute (not directly withdraw to an arbitrary address) any leftover dust once it exceeds a threshold.

### Proof of Concept
Analogous to the Curves PoC: for a given reward token, repeatedly call `queueNewRewards`/`donateRewards` with amounts that don't divide evenly by `totalStaked()`. After every staker calls `getReward`/`getRewards` and drains their full accrued balance, `IERC20(rewardToken).balanceOf(address(baseRewardPool))` will remain greater than zero, and this residual balance is unreachable through `earned()`/`_earned()``` for any account, growing further with each subsequent `queueNewRewards` call.

### Citations

**File:** rewards/BaseRewardPool.sol (L173-185)
```text
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

**File:** rewards/BaseRewardPoolV2.sol (L290-321)
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
    }

    function _earned(address _account, address _rewardToken, uint256 _userShare) internal view returns (uint256) {
        return ((_userShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**stakingTokenDecimals) + userRewards[_rewardToken][_account];
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L331-346)
```text
    function _queueNewRewardsWithoutTransfer(uint256 _amountReward, address _rewardToken) internal
    {
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards = rewardInfo.historicalRewards + _amountReward;
        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**vlMGPDecimal) / totalStaked();
        }
        emit ForfeitRewardAdded(_amountReward, _rewardToken);
```

**File:** rewards/mWOMSVBaseRewarder.sol (L305-328)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20Metadata(_rewardToken).safeTransferFrom(
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
                (_amountReward * 10**mWOMSVDecimal) / totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
    }
```

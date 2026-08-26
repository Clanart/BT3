# Q1757: BaseRewardPool.donateRewards - _sendReward zeroes userRewards before the transfer settles

## Question
Note that in rewards/BaseRewardPool.sol, _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken)` under V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue and force `rewards[_rewardToken].queuedRewards` apart from `rewards[_rewardToken].rewardPerTokenStored`, breaking the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue, then assert `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored` end identical in both runs.

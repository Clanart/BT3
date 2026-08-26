# Q0436: BaseRewardPool.donateRewards - _sendReward zeroes userRewards before the transfer settles

## Question
In rewards/BaseRewardPool.sol, _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Does `donateRewards(uint256 _amountReward, address _rewardToken)` let an unprivileged caller exploit that under the pool has exactly one registered reward token and no queued backlog, so that `totalStaked()` diverges from `IERC20(stakingToken).balanceOf(operator)`, the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool has exactly one registered reward token and no queued backlog, then assert `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)` end identical in both runs.

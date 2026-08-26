# Q3199: BaseRewardPool.donateRewards - _sendReward zeroes userRewards before the transfer settles

## Question
In rewards/BaseRewardPool.sol, _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken)` while the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, and drive `rewardTokens.length` out of agreement with `isRewardToken[_rewardToken]` - breaking the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned) under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, asserting on every row that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered.

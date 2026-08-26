# Q1874: BaseRewardPoolV2.donateRewards - queuedRewards flush timed by the attacker

## Question
In rewards/BaseRewardPoolV2.sol, _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Does `donateRewards(uint256 _amountReward, address _rewardToken)` let an unprivileged caller exploit that under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, so that `rewardTokens.length` diverges from `isRewardToken[_rewardToken]`, the invariant that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: queuedRewards flush timed by the attacker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, then assert `rewardTokens.length` and `isRewardToken[_rewardToken]` end identical in both runs.

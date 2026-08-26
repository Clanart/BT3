# Q5063: BaseRewardPool.donateRewards - queuedRewards flush timed by the attacker

## Question
Consider rewards/BaseRewardPool.sol, where _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Assuming the attacker calls the function twice in the same block to observe the second, early-continued iteration, can an unprivileged attacker turn this into a divergence between `rewardTokens.length` and `isRewardToken[_rewardToken]` via `donateRewards(uint256 _amountReward, address _rewardToken)`, breaking the invariant that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: queuedRewards flush timed by the attacker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Precondition: the attacker calls the function twice in the same block to observe the second, early-continued iteration.
- Invariant to test: a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls the function twice in the same block to observe the second, early-continued iteration, call `donateRewards(uint256 _amountReward, address _rewardToken)`, and assert `rewardTokens.length` equals `isRewardToken[_rewardToken]` and that no account can withdraw more than it put in.

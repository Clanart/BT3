# Q3522: BaseRewardPool.donateRewards - queuedRewards flush timed by the attacker

## Question
In rewards/BaseRewardPool.sol, _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Does `donateRewards(uint256 _amountReward, address _rewardToken)` let an unprivileged caller exploit that under a reward-manager queueNewRewards transaction is pending in the mempool, so that `totalStaked()` diverges from `IERC20(stakingToken).balanceOf(operator)`, the invariant that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: queuedRewards flush timed by the attacker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that a reward-manager queueNewRewards transaction is pending in the mempool, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier.

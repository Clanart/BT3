# Q3131: BaseRewardPool.donateRewards - queuedRewards flush timed by the attacker

## Question
Note that in rewards/BaseRewardPool.sol, _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken)` under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer and force `userRewards[_rewardToken][account]` apart from `earned(account,_rewardToken)`, breaking the invariant that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: queuedRewards flush timed by the attacker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier.

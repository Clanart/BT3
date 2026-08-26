# Q4231: BaseRewardPool.donateRewards - queuedRewards flush timed by the attacker

## Question
Consider rewards/BaseRewardPool.sol, where _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Assuming the victim has not been settled for several epochs and holds a large userRewards balance, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored` via `donateRewards(uint256 _amountReward, address _rewardToken)`, breaking the invariant that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: queuedRewards flush timed by the attacker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned) under the victim has not been settled for several epochs and holds a large userRewards balance, asserting on every row that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier.

# Q1025: BaseRewardPool.donateRewards - queuedRewards flush timed by the attacker

## Question
rewards/BaseRewardPool.sol - _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Can an unprivileged attacker controlling _amountReward down to one wei and which registered reward token is provisioned, under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken)` to break the reconciliation between `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` and the invariant that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: queuedRewards flush timed by the attacker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned) under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, asserting on every row that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier.

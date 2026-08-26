# Q0840: BaseRewardPoolV2.donateRewards - queuedRewards flush timed by the attacker

## Question
In rewards/BaseRewardPoolV2.sol, _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Starting from a state where rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `rewards[_rewardToken].historicalRewards` inconsistent with `IERC20(_rewardToken).balanceOf(address(this))`, violating the invariant that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: queuedRewards flush timed by the attacker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, then assert `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` end identical in both runs.

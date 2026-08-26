# Q3081: BaseRewardPoolV2.donateRewards - queuedRewards flush timed by the attacker

## Question
rewards/BaseRewardPoolV2.sol - _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Can an unprivileged attacker controlling _amountReward down to one wei and which registered reward token is provisioned, under a reward-manager queueNewRewards transaction is pending in the mempool, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken)` to break the reconciliation between `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)` and the invariant that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: queuedRewards flush timed by the attacker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that a reward-manager queueNewRewards transaction is pending in the mempool, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier.

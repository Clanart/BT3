# Q1659: BaseRewardPool.donateRewards - queuedRewards flush timed by the attacker

## Question
Note that in rewards/BaseRewardPool.sol, _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken)` under V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue and force `10**stakingDecimals()` apart from `totalStaked()`, breaking the invariant that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: queuedRewards flush timed by the attacker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Precondition: V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue.
- Invariant to test: a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `donateRewards(uint256 _amountReward, address _rewardToken)` sequence atomically under V1 recomputes stakingDecimals() by an external IERC20Metadata call on every accrual and its _updateFor has no early-continue, asserting at the end that `10**stakingDecimals()` still equals `totalStaked()` and the PoC's balance delta is non-positive.

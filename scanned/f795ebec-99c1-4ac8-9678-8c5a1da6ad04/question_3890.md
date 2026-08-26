# Q3890: BaseRewardPool.donateRewards - queuedRewards flush timed by the attacker

## Question
In rewards/BaseRewardPool.sol, _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Does `donateRewards(uint256 _amountReward, address _rewardToken)` let an unprivileged caller exploit that under the attacker funds the action with a flash loan of the staking token repaid in the same transaction, so that `balanceOf(account)` diverges from `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, the invariant that a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: queuedRewards flush timed by the attacker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: _provisionReward() only folds rewards[token].queuedRewards into the increment when totalStaked() != 0, and donateRewards is permissionless, so an attacker decides the exact stake distribution at the instant the entire backlog is released. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: a queued backlog must be released against the stake set that earned it, not against a set an attacker assembled one block earlier; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker funds the action with a flash loan of the staking token repaid in the same transaction, snapshot `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, run the attacker's `donateRewards(uint256 _amountReward, address _rewardToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

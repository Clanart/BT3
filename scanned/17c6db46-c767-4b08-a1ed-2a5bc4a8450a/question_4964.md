# Q4964: BaseRewardPool.updateFor - donation front-run of a legitimate queueNewRewards

## Question
In rewards/BaseRewardPool.sol, an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Does `updateFor(address _account)` let an unprivileged caller exploit that under the attacker calls the function twice in the same block to observe the second, early-continued iteration, so that `rewards[_rewardToken].historicalRewards` diverges from `IERC20(_rewardToken).balanceOf(address(this))`, the invariant that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: donation front-run of a legitimate queueNewRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Precondition: the attacker calls the function twice in the same block to observe the second, early-continued iteration.
- Invariant to test: the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the exact block in which their reward index is snapshotted) under the attacker calls the function twice in the same block to observe the second, early-continued iteration, asserting on every row that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block.

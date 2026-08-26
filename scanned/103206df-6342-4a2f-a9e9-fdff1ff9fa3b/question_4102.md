# Q4102: BaseRewardPool.updateFor - donation front-run of a legitimate queueNewRewards

## Question
rewards/BaseRewardPool.sol: an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Under the victim has not been settled for several epochs and holds a large userRewards balance, is there an unprivileged sequence of `updateFor(address _account)` that leaves `totalStaked()` unreconciled with `IERC20(stakingToken).balanceOf(operator)`, violates the invariant that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: donation front-run of a legitimate queueNewRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the victim has not been settled for several epochs and holds a large userRewards balance, fuzz the attacker inputs (the victim address and the exact block in which their reward index is snapshotted), and assert after every call that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block.

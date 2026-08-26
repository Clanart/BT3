# Q2150: BaseRewardPoolV2.updateFor - donation front-run of a legitimate queueNewRewards

## Question
rewards/BaseRewardPoolV2.sol: an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, is there an unprivileged sequence of `updateFor(address _account)` that leaves `10**stakingDecimals()` unreconciled with `totalStaked()`, violates the invariant that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: donation front-run of a legitimate queueNewRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the exact block in which their reward index is snapshotted) under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, asserting on every row that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block.

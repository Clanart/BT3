# Q2586: BaseRewardPoolV2.updateFor - donation front-run of a legitimate queueNewRewards

## Question
In rewards/BaseRewardPoolV2.sol, an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Starting from a state where the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, can an unprivileged EOA use `updateFor(address _account)` to leave `rewardTokens.length` inconsistent with `isRewardToken[_rewardToken]`, violating the invariant that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: donation front-run of a legitimate queueNewRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, fuzz the attacker inputs (the victim address and the exact block in which their reward index is snapshotted), and assert after every call that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block.

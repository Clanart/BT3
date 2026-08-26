# Q0746: BaseRewardPool.updateFor - donation front-run of a legitimate queueNewRewards

## Question
In rewards/BaseRewardPool.sol, an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Starting from a state where rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, can an unprivileged EOA use `updateFor(address _account)` to leave `balanceOf(account)` inconsistent with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, violating the invariant that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: donation front-run of a legitimate queueNewRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the exact block in which their reward index is snapshotted) under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, asserting on every row that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block.

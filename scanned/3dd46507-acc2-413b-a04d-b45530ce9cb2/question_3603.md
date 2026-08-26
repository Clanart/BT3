# Q3603: BaseRewardPoolV2.updateFor - donation front-run of a legitimate queueNewRewards

## Question
rewards/BaseRewardPoolV2.sol - an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Can an unprivileged attacker controlling the victim address and the exact block in which their reward index is snapshotted, under the victim has not been settled for several epochs and holds a large userRewards balance, exploit this through `updateFor(address _account)` to break the reconciliation between `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)` and the invariant that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: donation front-run of a legitimate queueNewRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the victim has not been settled for several epochs and holds a large userRewards balance, snapshot `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

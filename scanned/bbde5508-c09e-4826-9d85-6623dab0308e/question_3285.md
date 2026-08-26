# Q3285: BaseRewardPoolV2.updateFor - donation front-run of a legitimate queueNewRewards

## Question
Note that in rewards/BaseRewardPoolV2.sol, an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under the attacker funds the action with a flash loan of the staking token repaid in the same transaction and force `userRewards[_rewardToken][account]` apart from `earned(account,_rewardToken)`, breaking the invariant that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: donation front-run of a legitimate queueNewRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker funds the action with a flash loan of the staking token repaid in the same transaction, then assert `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` end identical in both runs.

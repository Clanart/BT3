# Q2706: BaseRewardPoolV2.donateRewards - donation front-run of a legitimate queueNewRewards

## Question
rewards/BaseRewardPoolV2.sol - an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Can an unprivileged attacker controlling _amountReward down to one wei and which registered reward token is provisioned, under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken)` to break the reconciliation between `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` and the invariant that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donation front-run of a legitimate queueNewRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, then assert `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` end identical in both runs.

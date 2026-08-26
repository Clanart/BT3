# Q4218: BaseRewardPool.donateRewards - donation front-run of a legitimate queueNewRewards

## Question
rewards/BaseRewardPool.sol - an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Can an unprivileged attacker controlling _amountReward down to one wei and which registered reward token is provisioned, under the victim has not been settled for several epochs and holds a large userRewards balance, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken)` to break the reconciliation between `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` and the invariant that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donation front-run of a legitimate queueNewRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned) under the victim has not been settled for several epochs and holds a large userRewards balance, asserting on every row that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block.

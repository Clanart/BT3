# Q3064: BaseRewardPoolV2.donateRewards - donation front-run of a legitimate queueNewRewards

## Question
Consider rewards/BaseRewardPoolV2.sol, where an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Assuming a reward-manager queueNewRewards transaction is pending in the mempool, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` via `donateRewards(uint256 _amountReward, address _rewardToken)`, breaking the invariant that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donation front-run of a legitimate queueNewRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a reward-manager queueNewRewards transaction is pending in the mempool, have the attacker run `donateRewards(uint256 _amountReward, address _rewardToken)`, then assert the victim's claimable value and the `userRewards[_rewardToken][account]` versus `earned(account,_rewardToken)` relation are unchanged by the attacker's transaction.

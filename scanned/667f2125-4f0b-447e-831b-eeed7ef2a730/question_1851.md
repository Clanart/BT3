# Q1851: BaseRewardPoolV2.donateRewards - donation front-run of a legitimate queueNewRewards

## Question
In rewards/BaseRewardPoolV2.sol, an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Does `donateRewards(uint256 _amountReward, address _rewardToken)` let an unprivileged caller exploit that under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, so that `10**stakingDecimals()` diverges from `totalStaked()`, the invariant that the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donation front-run of a legitimate queueNewRewards)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: an attacker inflates totalStaked() (which reads IERC20(stakingToken).balanceOf(operator)) in the block before the reward manager calls queueNewRewards, so the manager's distribution is divided by an inflated denominator and truncates. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: the reward-per-token increment for a queued distribution must not be reducible by a third party in the same block; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, snapshot `10**stakingDecimals()` and `totalStaked()`, run the attacker's `donateRewards(uint256 _amountReward, address _rewardToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

# Q1897: BaseRewardPoolV2.donateRewards - first-staker capture of the queued backlog

## Question
In rewards/BaseRewardPoolV2.sol, while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Does `donateRewards(uint256 _amountReward, address _rewardToken)` let an unprivileged caller exploit that under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, so that `rewards[_rewardToken].rewardPerTokenStored` diverges from `userRewardPerTokenPaid[_rewardToken][account]`, the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: first-staker capture of the queued backlog)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block depositor; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned) under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, asserting on every row that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor.

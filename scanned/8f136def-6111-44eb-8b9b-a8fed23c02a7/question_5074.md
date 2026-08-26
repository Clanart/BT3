# Q5074: BaseRewardPool.donateRewards - first-staker capture of the queued backlog

## Question
Note that in rewards/BaseRewardPool.sol, while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken)` under the attacker calls the function twice in the same block to observe the second, early-continued iteration and force `rewards[_rewardToken].rewardPerTokenStored` apart from `userRewardPerTokenPaid[_rewardToken][account]`, breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: first-staker capture of the queued backlog)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Precondition: the attacker calls the function twice in the same block to observe the second, early-continued iteration.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block depositor; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned) under the attacker calls the function twice in the same block to observe the second, early-continued iteration, asserting on every row that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor.

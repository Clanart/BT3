# Q3148: BaseRewardPool.donateRewards - first-staker capture of the queued backlog

## Question
In rewards/BaseRewardPool.sol, while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken)` while the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, and drive `totalStaked()` out of agreement with `IERC20(stakingToken).balanceOf(operator)` - breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: first-staker capture of the queued backlog)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block depositor; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned) under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, asserting on every row that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor.

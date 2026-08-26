# Q4244: BaseRewardPool.donateRewards - first-staker capture of the queued backlog

## Question
In rewards/BaseRewardPool.sol, while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken)` while the victim has not been settled for several epochs and holds a large userRewards balance, and drive `rewards[_rewardToken].historicalRewards` out of agreement with `IERC20(_rewardToken).balanceOf(address(this))` - breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: first-staker capture of the queued backlog)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block depositor; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned) under the victim has not been settled for several epochs and holds a large userRewards balance, asserting on every row that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor.

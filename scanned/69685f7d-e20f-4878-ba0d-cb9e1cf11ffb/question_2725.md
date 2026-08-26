# Q2725: BaseRewardPool.donateRewards - first-staker capture of the queued backlog

## Question
rewards/BaseRewardPool.sol: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. With _amountReward down to one wei and which registered reward token is provisioned under attacker control and the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, can an unprivileged caller sequence `donateRewards(uint256 _amountReward, address _rewardToken)` so that `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` no longer reconcile, violating the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: first-staker capture of the queued backlog)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block depositor; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor.

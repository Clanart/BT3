# Q2334: BaseRewardPoolV2.donateRewards - first-staker capture of the queued backlog

## Question
Consider rewards/BaseRewardPoolV2.sol, where while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Assuming the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` via `donateRewards(uint256 _amountReward, address _rewardToken)`, breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: first-staker capture of the queued backlog)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block depositor; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, then assert `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` end identical in both runs.

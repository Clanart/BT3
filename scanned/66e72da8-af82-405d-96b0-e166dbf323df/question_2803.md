# Q2803: BaseRewardPool.donateRewards - early-continue skips a genuine balance change

## Question
Note that in rewards/BaseRewardPool.sol, the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken)` under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small and force `rewards[_rewardToken].rewardPerTokenStored` apart from `userRewardPerTokenPaid[_rewardToken][account]`, breaking the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, then assert `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` end identical in both runs.

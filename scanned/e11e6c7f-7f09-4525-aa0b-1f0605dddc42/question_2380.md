# Q2380: BaseRewardPoolV2.donateRewards - early-continue skips a genuine balance change

## Question
rewards/BaseRewardPoolV2.sol: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, is there an unprivileged sequence of `donateRewards(uint256 _amountReward, address _rewardToken)` that leaves `rewards[_rewardToken].rewardPerTokenStored` unreconciled with `userRewardPerTokenPaid[_rewardToken][account]`, violates the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, snapshot `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]`, run the attacker's `donateRewards(uint256 _amountReward, address _rewardToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

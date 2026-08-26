# Q1943: BaseRewardPoolV2.donateRewards - early-continue skips a genuine balance change

## Question
Consider rewards/BaseRewardPoolV2.sol, where the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Assuming the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, can an unprivileged attacker turn this into a divergence between `rewardTokens.length` and `isRewardToken[_rewardToken]` via `donateRewards(uint256 _amountReward, address _rewardToken)`, breaking the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, then assert `rewardTokens.length` and `isRewardToken[_rewardToken]` end identical in both runs.

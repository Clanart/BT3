# Q2310: BaseRewardPool.donateRewards - early-continue skips a genuine balance change

## Question
rewards/BaseRewardPool.sol: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. With _amountReward down to one wei and which registered reward token is provisioned under attacker control and the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, can an unprivileged caller sequence `donateRewards(uint256 _amountReward, address _rewardToken)` so that `rewardTokens.length` and `isRewardToken[_rewardToken]` no longer reconcile, violating the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned) under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, asserting on every row that userRewards must capture every balance-weighted segment, including segments where the global index did not move.

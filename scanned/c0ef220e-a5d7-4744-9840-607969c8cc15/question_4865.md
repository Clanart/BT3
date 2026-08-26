# Q4865: BaseRewardPool.donateRewards - early-continue skips a genuine balance change

## Question
rewards/BaseRewardPool.sol - the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Can an unprivileged attacker controlling _amountReward down to one wei and which registered reward token is provisioned, under a previously registered reward token has begun reverting on transfer, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken)` to break the reconciliation between `10**stakingDecimals()` and `totalStaked()` and the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned) under a previously registered reward token has begun reverting on transfer, asserting on every row that userRewards must capture every balance-weighted segment, including segments where the global index did not move.

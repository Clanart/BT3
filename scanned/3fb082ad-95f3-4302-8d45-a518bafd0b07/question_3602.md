# Q3602: BaseRewardPool.donateRewards - early-continue skips a genuine balance change

## Question
In rewards/BaseRewardPool.sol, the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Does `donateRewards(uint256 _amountReward, address _rewardToken)` let an unprivileged caller exploit that under a reward-manager queueNewRewards transaction is pending in the mempool, so that `totalStaked()` diverges from `IERC20(stakingToken).balanceOf(operator)`, the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned) under a reward-manager queueNewRewards transaction is pending in the mempool, asserting on every row that userRewards must capture every balance-weighted segment, including segments where the global index did not move.

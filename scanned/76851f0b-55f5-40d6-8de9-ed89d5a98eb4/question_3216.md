# Q3216: BaseRewardPool.donateRewards - early-continue skips a genuine balance change

## Question
In rewards/BaseRewardPool.sol, the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Starting from a state where the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `userRewards[_rewardToken][account]` inconsistent with `earned(account,_rewardToken)`, violating the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `donateRewards(uint256 _amountReward, address _rewardToken)` sequence atomically under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, asserting at the end that `userRewards[_rewardToken][account]` still equals `earned(account,_rewardToken)` and the PoC's balance delta is non-positive.

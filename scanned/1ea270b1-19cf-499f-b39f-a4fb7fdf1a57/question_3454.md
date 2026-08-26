# Q3454: BaseRewardPool.updateFor - early-continue skips a genuine balance change

## Question
In rewards/BaseRewardPool.sol, the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Can an unprivileged attacker reach this through `updateFor(address _account)` while a reward-manager queueNewRewards transaction is pending in the mempool, and drive `userRewards[_rewardToken][account]` out of agreement with `earned(account,_rewardToken)` - breaking the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a reward-manager queueNewRewards transaction is pending in the mempool, have the attacker run `updateFor(address _account)`, then assert the victim's claimable value and the `userRewards[_rewardToken][account]` versus `earned(account,_rewardToken)` relation are unchanged by the attacker's transaction.

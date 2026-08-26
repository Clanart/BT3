# Q3063: BaseRewardPool.updateFor - early-continue skips a genuine balance change

## Question
rewards/BaseRewardPool.sol: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. With the victim address and the exact block in which their reward index is snapshotted under attacker control and the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, can an unprivileged caller sequence `updateFor(address _account)` so that `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` no longer reconcile, violating the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, have the attacker run `updateFor(address _account)`, then assert the victim's claimable value and the `rewards[_rewardToken].rewardPerTokenStored` versus `userRewardPerTokenPaid[_rewardToken][account]` relation are unchanged by the attacker's transaction.

# Q3010: BaseRewardPoolV2.updateFor - early-continue skips a genuine balance change

## Question
rewards/BaseRewardPoolV2.sol - the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Can an unprivileged attacker controlling the victim address and the exact block in which their reward index is snapshotted, under a reward-manager queueNewRewards transaction is pending in the mempool, exploit this through `updateFor(address _account)` to break the reconciliation between `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` and the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that a reward-manager queueNewRewards transaction is pending in the mempool, fuzz the attacker inputs (the victim address and the exact block in which their reward index is snapshotted), and assert after every call that userRewards must capture every balance-weighted segment, including segments where the global index did not move.

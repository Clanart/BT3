# Q4479: BaseRewardPoolV2.updateFor - early-continue skips a genuine balance change

## Question
rewards/BaseRewardPoolV2.sol: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. With the victim address and the exact block in which their reward index is snapshotted under attacker control and the attacker calls the function twice in the same block to observe the second, early-continued iteration, can an unprivileged caller sequence `updateFor(address _account)` so that `10**stakingDecimals()` and `totalStaked()` no longer reconcile, violating the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the attacker calls the function twice in the same block to observe the second, early-continued iteration.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the exact block in which their reward index is snapshotted) under the attacker calls the function twice in the same block to observe the second, early-continued iteration, asserting on every row that userRewards must capture every balance-weighted segment, including segments where the global index did not move.

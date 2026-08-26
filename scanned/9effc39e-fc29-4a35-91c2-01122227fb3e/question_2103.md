# Q2103: BaseRewardPool.updateFor - early-continue skips a genuine balance change

## Question
rewards/BaseRewardPool.sol: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. With the victim address and the exact block in which their reward index is snapshotted under attacker control and the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, can an unprivileged caller sequence `updateFor(address _account)` so that `10**stakingDecimals()` and `totalStaked()` no longer reconcile, violating the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, fuzz the attacker inputs (the victim address and the exact block in which their reward index is snapshotted), and assert after every call that userRewards must capture every balance-weighted segment, including segments where the global index did not move.

# Q0901: BaseRewardPool.updateFor - early-continue skips a genuine balance change

## Question
rewards/BaseRewardPool.sol: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Under rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, is there an unprivileged sequence of `updateFor(address _account)` that leaves `rewards[_rewardToken].queuedRewards` unreconciled with `rewards[_rewardToken].rewardPerTokenStored`, violates the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, fuzz the attacker inputs (the victim address and the exact block in which their reward index is snapshotted), and assert after every call that userRewards must capture every balance-weighted segment, including segments where the global index did not move.

# Q0188: BaseRewardPool.updateFor - early-continue skips a genuine balance change

## Question
Note that in rewards/BaseRewardPool.sol, the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under the pool has exactly one registered reward token and no queued backlog and force `balanceOf(account)` apart from `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, breaking the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the pool has exactly one registered reward token and no queued backlog, fuzz the attacker inputs (the victim address and the exact block in which their reward index is snapshotted), and assert after every call that userRewards must capture every balance-weighted segment, including segments where the global index did not move.

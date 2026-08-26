# Q0127: BaseRewardPoolV2.updateFor - early-continue skips a genuine balance change

## Question
rewards/BaseRewardPoolV2.sol: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. With the victim address and the exact block in which their reward index is snapshotted under attacker control and the pool has exactly one registered reward token and no queued backlog, can an unprivileged caller sequence `updateFor(address _account)` so that `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` no longer reconcile, violating the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under the pool has exactly one registered reward token and no queued backlog, asserting at the end that `balanceOf(account)` still equals `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` and the PoC's balance delta is non-positive.

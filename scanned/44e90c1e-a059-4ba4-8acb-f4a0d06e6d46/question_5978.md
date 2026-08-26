# Q5978: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips updatePool

## Question
rewards/MasterMagpie.sol: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Under the attacker repeats the call in the same block to observe the second, no-op iteration, is there an unprivileged sequence of `emergencyWithdraw(address _stakingToken)` that leaves `IBaseRewardPool(rewarder).balanceOf(user)` unreconciled with `IBaseRewardPool(rewarder).totalStaked()`, violates the invariant that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Precondition: the attacker repeats the call in the same block to observe the second, no-op iteration.
- Invariant to test: rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_stakingToken and the exact block in which the pool is paused) under the attacker repeats the call in the same block to observe the second, no-op iteration, asserting on every row that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare.

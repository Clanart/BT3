# Q2859: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips updatePool

## Question
In rewards/MasterMagpie.sol, emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Can an unprivileged attacker reach this through `emergencyWithdraw(address _stakingToken)` while the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, and drive `IBaseRewardPool(rewarder).balanceOf(user)` out of agreement with `IBaseRewardPool(rewarder).totalStaked()` - breaking the invariant that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_stakingToken and the exact block in which the pool is paused) under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, asserting on every row that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare.

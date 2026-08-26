# Q4176: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips updatePool

## Question
In rewards/MasterMagpie.sol, emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Does `emergencyWithdraw(address _stakingToken)` let an unprivileged caller exploit that under the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, so that `vlmgp.totalSupply()` diverges from `sum of userInfo[vlmgp][*].amount`, the invariant that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty.
- Invariant to test: rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `emergencyWithdraw(address _stakingToken)`: constrain the setup so that the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, fuzz the attacker inputs (_stakingToken and the exact block in which the pool is paused), and assert after every call that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare.

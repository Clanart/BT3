# Q3617: MasterMagpie.updatePool - emergencyWithdraw skips updatePool

## Question
rewards/MasterMagpie.sol: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Under a large honest deposit is sitting in the mempool and the attacker sandwiches it, is there an unprivileged sequence of `updatePool(address _stakingToken)` that leaves `vlmgp.totalSupply()` unreconciled with `sum of userInfo[vlmgp][*].amount`, violates the invariant that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: emergencyWithdraw skips updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Precondition: a large honest deposit is sitting in the mempool and the attacker sandwiches it.
- Invariant to test: rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updatePool(address _stakingToken)`: constrain the setup so that a large honest deposit is sitting in the mempool and the attacker sandwiches it, fuzz the attacker inputs (_stakingToken and the timestamp at which accMGPPerShare is rolled forward), and assert after every call that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare.

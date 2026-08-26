# Q5621: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips updatePool

## Question
Consider rewards/MasterMagpie.sol, where emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Assuming the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, can an unprivileged attacker turn this into a divergence between `userInfo[_stakingToken][user].rewardDebt` and `tokenToPoolInfo[_stakingToken].accMGPPerShare` via `emergencyWithdraw(address _stakingToken)`, breaking the invariant that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Precondition: the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp.
- Invariant to test: rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare; concretely, `userInfo[_stakingToken][user].rewardDebt` must stay reconciled with `tokenToPoolInfo[_stakingToken].accMGPPerShare`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `emergencyWithdraw(address _stakingToken)`: constrain the setup so that the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, fuzz the attacker inputs (_stakingToken and the exact block in which the pool is paused), and assert after every call that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare.

# Q5898: MasterMagpie.emergencyWithdraw - emergencyWithdraw skips updatePool

## Question
rewards/MasterMagpie.sol: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, is there an unprivileged sequence of `emergencyWithdraw(address _stakingToken)` that leaves `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` unreconciled with `block.timestamp`, violates the invariant that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `emergencyWithdraw(address _stakingToken)` (mechanism: emergencyWithdraw skips updatePool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `emergencyWithdraw(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the exact block in which the pool is paused
- Exploit idea: emergencyWithdraw() writes rewardDebt from the stale tokenToPoolInfo.accMGPPerShare without calling updatePool() first, so MGP accrued since lastRewardTimestamp is silently rebased away for the withdrawing user or over-credited on re-entry. Precondition: the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18.
- Invariant to test: rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `emergencyWithdraw(address _stakingToken)`: constrain the setup so that the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, fuzz the attacker inputs (_stakingToken and the exact block in which the pool is paused), and assert after every call that rewardDebt must only ever be written against a freshly rolled-forward accMGPPerShare.

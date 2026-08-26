# Q1086: MasterMagpie.updatePool - allocPoint / totalAllocPoint rounding starves a pool

## Question
rewards/MasterMagpie.sol - updatePool() computes mgpReward = multiplier * mgpPerSec * pool.allocPoint / totalAllocPoint and then (mgpReward * 1e12) / lpSupply, so an attacker who maximises lpSupply at the moment of the roll-forward makes the second division truncate the whole slice to zero. Can an unprivileged attacker controlling _stakingToken and the timestamp at which accMGPPerShare is rolled forward, under the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, exploit this through `updatePool(address _stakingToken)` to break the reconciliation between `unClaimedMgp[_stakingToken][user]` and `userInfo[_stakingToken][user].rewardDebt` and the invariant that MGP accrued for an interval must not be destroyable by a third party choosing when the roll-forward happens, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: allocPoint / totalAllocPoint rounding starves a pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: updatePool() computes mgpReward = multiplier * mgpPerSec * pool.allocPoint / totalAllocPoint and then (mgpReward * 1e12) / lpSupply, so an attacker who maximises lpSupply at the moment of the roll-forward makes the second division truncate the whole slice to zero. Precondition: the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it.
- Invariant to test: MGP accrued for an interval must not be destroyable by a third party choosing when the roll-forward happens; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, then assert `unClaimedMgp[_stakingToken][user]` and `userInfo[_stakingToken][user].rewardDebt` end identical in both runs.

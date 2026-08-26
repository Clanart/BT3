# Q5828: MasterMagpie.updatePool - allocPoint / totalAllocPoint rounding starves a pool

## Question
Consider rewards/MasterMagpie.sol, where updatePool() computes mgpReward = multiplier * mgpPerSec * pool.allocPoint / totalAllocPoint and then (mgpReward * 1e12) / lpSupply, so an attacker who maximises lpSupply at the moment of the roll-forward makes the second division truncate the whole slice to zero. Assuming the victim has a large unClaimedMgp balance that has not been settled for several epochs, can an unprivileged attacker turn this into a divergence between `unClaimedMgp[_stakingToken][user]` and `userInfo[_stakingToken][user].rewardDebt` via `updatePool(address _stakingToken)`, breaking the invariant that MGP accrued for an interval must not be destroyable by a third party choosing when the roll-forward happens and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: allocPoint / totalAllocPoint rounding starves a pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: updatePool() computes mgpReward = multiplier * mgpPerSec * pool.allocPoint / totalAllocPoint and then (mgpReward * 1e12) / lpSupply, so an attacker who maximises lpSupply at the moment of the roll-forward makes the second division truncate the whole slice to zero. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: MGP accrued for an interval must not be destroyable by a third party choosing when the roll-forward happens; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `updatePool(address _stakingToken)`: constrain the setup so that the victim has a large unClaimedMgp balance that has not been settled for several epochs, fuzz the attacker inputs (_stakingToken and the timestamp at which accMGPPerShare is rolled forward), and assert after every call that MGP accrued for an interval must not be destroyable by a third party choosing when the roll-forward happens.

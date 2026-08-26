# Q5641: MasterMagpie.updatePool - allocPoint / totalAllocPoint rounding starves a pool

## Question
rewards/MasterMagpie.sol: updatePool() computes mgpReward = multiplier * mgpPerSec * pool.allocPoint / totalAllocPoint and then (mgpReward * 1e12) / lpSupply, so an attacker who maximises lpSupply at the moment of the roll-forward makes the second division truncate the whole slice to zero. Under the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, is there an unprivileged sequence of `updatePool(address _stakingToken)` that leaves `userInfo[_stakingToken][user].available` unreconciled with `userInfo[_stakingToken][user].amount`, violates the invariant that MGP accrued for an interval must not be destroyable by a third party choosing when the roll-forward happens, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: allocPoint / totalAllocPoint rounding starves a pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: updatePool() computes mgpReward = multiplier * mgpPerSec * pool.allocPoint / totalAllocPoint and then (mgpReward * 1e12) / lpSupply, so an attacker who maximises lpSupply at the moment of the roll-forward makes the second division truncate the whole slice to zero. Precondition: the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp.
- Invariant to test: MGP accrued for an interval must not be destroyable by a third party choosing when the roll-forward happens; concretely, `userInfo[_stakingToken][user].available` must stay reconciled with `userInfo[_stakingToken][user].amount`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `updatePool(address _stakingToken)`: constrain the setup so that the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, fuzz the attacker inputs (_stakingToken and the timestamp at which accMGPPerShare is rolled forward), and assert after every call that MGP accrued for an interval must not be destroyable by a third party choosing when the roll-forward happens.

# Q3633: MasterMagpie.updatePool - allocPoint / totalAllocPoint rounding starves a pool

## Question
rewards/MasterMagpie.sol: updatePool() computes mgpReward = multiplier * mgpPerSec * pool.allocPoint / totalAllocPoint and then (mgpReward * 1e12) / lpSupply, so an attacker who maximises lpSupply at the moment of the roll-forward makes the second division truncate the whole slice to zero. Under a large honest deposit is sitting in the mempool and the attacker sandwiches it, is there an unprivileged sequence of `updatePool(address _stakingToken)` that leaves `IBaseRewardPool(rewarder).balanceOf(user)` unreconciled with `IBaseRewardPool(rewarder).totalStaked()`, violates the invariant that MGP accrued for an interval must not be destroyable by a third party choosing when the roll-forward happens, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: allocPoint / totalAllocPoint rounding starves a pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: updatePool() computes mgpReward = multiplier * mgpPerSec * pool.allocPoint / totalAllocPoint and then (mgpReward * 1e12) / lpSupply, so an attacker who maximises lpSupply at the moment of the roll-forward makes the second division truncate the whole slice to zero. Precondition: a large honest deposit is sitting in the mempool and the attacker sandwiches it.
- Invariant to test: MGP accrued for an interval must not be destroyable by a third party choosing when the roll-forward happens; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a large honest deposit is sitting in the mempool and the attacker sandwiches it, have the attacker run `updatePool(address _stakingToken)`, then assert the victim's claimable value and the `IBaseRewardPool(rewarder).balanceOf(user)` versus `IBaseRewardPool(rewarder).totalStaked()` relation are unchanged by the attacker's transaction.

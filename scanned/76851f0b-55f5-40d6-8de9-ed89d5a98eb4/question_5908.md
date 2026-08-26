# Q5908: MasterMagpie.updatePool - allocPoint / totalAllocPoint rounding starves a pool

## Question
Consider rewards/MasterMagpie.sol, where updatePool() computes mgpReward = multiplier * mgpPerSec * pool.allocPoint / totalAllocPoint and then (mgpReward * 1e12) / lpSupply, so an attacker who maximises lpSupply at the moment of the roll-forward makes the second division truncate the whole slice to zero. Assuming the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, can an unprivileged attacker turn this into a divergence between `_calLpSupply(_stakingToken)` and `IERC20(_stakingToken).balanceOf(masterMagpie)` via `updatePool(address _stakingToken)`, breaking the invariant that MGP accrued for an interval must not be destroyable by a third party choosing when the roll-forward happens and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: allocPoint / totalAllocPoint rounding starves a pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: updatePool() computes mgpReward = multiplier * mgpPerSec * pool.allocPoint / totalAllocPoint and then (mgpReward * 1e12) / lpSupply, so an attacker who maximises lpSupply at the moment of the roll-forward makes the second division truncate the whole slice to zero. Precondition: the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18.
- Invariant to test: MGP accrued for an interval must not be destroyable by a third party choosing when the roll-forward happens; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_stakingToken and the timestamp at which accMGPPerShare is rolled forward) under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, asserting on every row that MGP accrued for an interval must not be destroyable by a third party choosing when the roll-forward happens.

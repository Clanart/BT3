# Q5748: MasterMagpie.updatePool - allocPoint / totalAllocPoint rounding starves a pool

## Question
Consider rewards/MasterMagpie.sol, where updatePool() computes mgpReward = multiplier * mgpPerSec * pool.allocPoint / totalAllocPoint and then (mgpReward * 1e12) / lpSupply, so an attacker who maximises lpSupply at the moment of the roll-forward makes the second division truncate the whole slice to zero. Assuming the contract is paused so only emergencyWithdraw is reachable, can an unprivileged attacker turn this into a divergence between `userInfo[_stakingToken][user].rewardDebt` and `tokenToPoolInfo[_stakingToken].accMGPPerShare` via `updatePool(address _stakingToken)`, breaking the invariant that MGP accrued for an interval must not be destroyable by a third party choosing when the roll-forward happens and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: allocPoint / totalAllocPoint rounding starves a pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: updatePool() computes mgpReward = multiplier * mgpPerSec * pool.allocPoint / totalAllocPoint and then (mgpReward * 1e12) / lpSupply, so an attacker who maximises lpSupply at the moment of the roll-forward makes the second division truncate the whole slice to zero. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: MGP accrued for an interval must not be destroyable by a third party choosing when the roll-forward happens; concretely, `userInfo[_stakingToken][user].rewardDebt` must stay reconciled with `tokenToPoolInfo[_stakingToken].accMGPPerShare`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the contract is paused so only emergencyWithdraw is reachable, have the attacker run `updatePool(address _stakingToken)`, then assert the victim's claimable value and the `userInfo[_stakingToken][user].rewardDebt` versus `tokenToPoolInfo[_stakingToken].accMGPPerShare` relation are unchanged by the attacker's transaction.

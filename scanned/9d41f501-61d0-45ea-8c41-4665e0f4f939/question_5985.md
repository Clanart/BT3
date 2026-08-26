# Q5985: MasterMagpie.updatePool - lastRewardTimestamp advanced on an empty pool

## Question
In rewards/MasterMagpie.sol, updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Can an unprivileged attacker reach this through `updatePool(address _stakingToken)` while the attacker repeats the call in the same block to observe the second, no-op iteration, and drive `IBaseRewardPool(rewarder).balanceOf(user)` out of agreement with `IBaseRewardPool(rewarder).totalStaked()` - breaking the invariant that emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lastRewardTimestamp advanced on an empty pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Precondition: the attacker repeats the call in the same block to observe the second, no-op iteration.
- Invariant to test: emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker repeats the call in the same block to observe the second, no-op iteration, call `updatePool(address _stakingToken)`, and assert `IBaseRewardPool(rewarder).balanceOf(user)` equals `IBaseRewardPool(rewarder).totalStaked()` and that no account can withdraw more than it put in.

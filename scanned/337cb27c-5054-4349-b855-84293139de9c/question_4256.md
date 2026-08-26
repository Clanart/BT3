# Q4256: MasterMagpie.updatePool - lastRewardTimestamp advanced on an empty pool

## Question
rewards/MasterMagpie.sol: updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Under the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, is there an unprivileged sequence of `updatePool(address _stakingToken)` that leaves `vlmgp.totalSupply()` unreconciled with `sum of userInfo[vlmgp][*].amount`, violates the invariant that emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lastRewardTimestamp advanced on an empty pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty.
- Invariant to test: emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, snapshot `vlmgp.totalSupply()` and `sum of userInfo[vlmgp][*].amount`, run the attacker's `updatePool(address _stakingToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

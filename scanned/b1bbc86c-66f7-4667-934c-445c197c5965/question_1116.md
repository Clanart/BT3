# Q1116: MasterMagpie.updatePool - lastRewardTimestamp advanced on an empty pool

## Question
In rewards/MasterMagpie.sol, updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Starting from a state where the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, can an unprivileged EOA use `updatePool(address _stakingToken)` to leave `_calLpSupply(_stakingToken)` inconsistent with `IERC20(_stakingToken).balanceOf(masterMagpie)`, violating the invariant that emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lastRewardTimestamp advanced on an empty pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Precondition: the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it.
- Invariant to test: emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_stakingToken and the timestamp at which accMGPPerShare is rolled forward) under the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, asserting on every row that emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create.

# Q4759: MasterMagpie.updatePool - lastRewardTimestamp advanced on an empty pool

## Question
Note that in rewards/MasterMagpie.sol, updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Can an attacker holding only tokens bought on market reach it via `updatePool(address _stakingToken)` under the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction and force `mgpPerSec` apart from `IERC20(mgp).balanceOf(masterMagpie)`, breaking the invariant that emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lastRewardTimestamp advanced on an empty pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Precondition: the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction.
- Invariant to test: emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction, snapshot `mgpPerSec` and `IERC20(mgp).balanceOf(masterMagpie)`, run the attacker's `updatePool(address _stakingToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

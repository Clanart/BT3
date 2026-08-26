# Q3649: MasterMagpie.updatePool - lastRewardTimestamp advanced on an empty pool

## Question
In rewards/MasterMagpie.sol, updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Can an unprivileged attacker reach this through `updatePool(address _stakingToken)` while a large honest deposit is sitting in the mempool and the attacker sandwiches it, and drive `totalAllocPoint` out of agreement with `tokenToPoolInfo[_stakingToken].allocPoint` - breaking the invariant that emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lastRewardTimestamp advanced on an empty pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Precondition: a large honest deposit is sitting in the mempool and the attacker sandwiches it.
- Invariant to test: emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large honest deposit is sitting in the mempool and the attacker sandwiches it, call `updatePool(address _stakingToken)`, and assert `totalAllocPoint` equals `tokenToPoolInfo[_stakingToken].allocPoint` and that no account can withdraw more than it put in.

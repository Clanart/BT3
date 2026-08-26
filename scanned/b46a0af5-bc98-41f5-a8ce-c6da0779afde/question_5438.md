# Q5438: MasterMagpie.updatePool - lastRewardTimestamp advanced on an empty pool

## Question
In rewards/MasterMagpie.sol, updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Does `updatePool(address _stakingToken)` let an unprivileged caller exploit that under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), so that `userInfo[_stakingToken][user].available` diverges from `userInfo[_stakingToken][user].amount`, the invariant that emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lastRewardTimestamp advanced on an empty pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked().
- Invariant to test: emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create; concretely, `userInfo[_stakingToken][user].available` must stay reconciled with `userInfo[_stakingToken][user].amount`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `updatePool(address _stakingToken)`: constrain the setup so that the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), fuzz the attacker inputs (_stakingToken and the timestamp at which accMGPPerShare is rolled forward), and assert after every call that emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create.

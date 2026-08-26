# Q5158: MasterMagpie.updatePool - lastRewardTimestamp advanced on an empty pool

## Question
In rewards/MasterMagpie.sol, updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Does `updatePool(address _stakingToken)` let an unprivileged caller exploit that under the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, so that `userInfo[_stakingToken][user].amount` diverges from `_calLpSupply(_stakingToken)`, the invariant that emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lastRewardTimestamp advanced on an empty pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Precondition: the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals.
- Invariant to test: emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, call `updatePool(address _stakingToken)`, and assert `userInfo[_stakingToken][user].amount` equals `_calLpSupply(_stakingToken)` and that no account can withdraw more than it put in.

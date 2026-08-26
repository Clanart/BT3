# Q5645: MasterMagpie.updatePool - lastRewardTimestamp advanced on an empty pool

## Question
rewards/MasterMagpie.sol - updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Can an unprivileged attacker controlling _stakingToken and the timestamp at which accMGPPerShare is rolled forward, under the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, exploit this through `updatePool(address _stakingToken)` to break the reconciliation between `userInfo[_stakingToken][user].rewardDebt` and `tokenToPoolInfo[_stakingToken].accMGPPerShare` and the invariant that emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: lastRewardTimestamp advanced on an empty pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: updatePool() returns early after setting pool.lastRewardTimestamp = block.timestamp whenever lpSupply == 0, so an attacker who empties a pool and pokes updatePool burns the elapsed emission window for everyone who re-enters. Precondition: the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp.
- Invariant to test: emission owed to a pool for an interval must not be destroyed by a transient zero-supply state a third party can create; concretely, `userInfo[_stakingToken][user].rewardDebt` must stay reconciled with `tokenToPoolInfo[_stakingToken].accMGPPerShare`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, snapshot `userInfo[_stakingToken][user].rewardDebt` and `tokenToPoolInfo[_stakingToken].accMGPPerShare`, run the attacker's `updatePool(address _stakingToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

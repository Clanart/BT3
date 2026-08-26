# Q5110: WombatStaking.harvest - harvest is permissionless and drives the whole fee split

## Question
wombat/WombatStaking.sol: harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. With _lpToken and the timing of every harvest-driven fee split under attacker control and a large honest deposit is pending in the mempool for the same pool, can an unprivileged caller sequence `harvest(address _lpToken)` so that `feeInfos[i].value` and `totalFee` no longer reconcile, violating the invariant that the timing of protocol fee conversion must not be selectable by an unrelated party and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest is permissionless and drives the whole fee split)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Precondition: a large honest deposit is pending in the mempool for the same pool.
- Invariant to test: the timing of protocol fee conversion must not be selectable by an unrelated party; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `harvest(address _lpToken)` sequence atomically under a large honest deposit is pending in the mempool for the same pool, asserting at the end that `feeInfos[i].value` still equals `totalFee` and the PoC's balance delta is non-positive.

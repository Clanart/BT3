# Q4062: WombatStaking.harvest - harvest is permissionless and drives the whole fee split

## Question
wombat/WombatStaking.sol: harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. With _lpToken and the timing of every harvest-driven fee split under attacker control and several feeInfos entries are active at once and the harvested amount is small, can an unprivileged caller sequence `harvest(address _lpToken)` so that `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid` no longer reconcile, violating the invariant that the timing of protocol fee conversion must not be selectable by an unrelated party and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest is permissionless and drives the whole fee split)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: the timing of protocol fee conversion must not be selectable by an unrelated party; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under several feeInfos entries are active at once and the harvested amount is small, then assert `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid` end identical in both runs.

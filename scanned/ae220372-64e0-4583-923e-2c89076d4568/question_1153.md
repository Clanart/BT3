# Q1153: WombatStaking.harvest - harvest is permissionless and drives the whole fee split

## Question
wombat/WombatStaking.sol - harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Can an unprivileged attacker controlling _lpToken and the timing of every harvest-driven fee split, under the contract is holding WOM collected as a protocol fee that has not yet been split, exploit this through `harvest(address _lpToken)` to break the reconciliation between `feeInfos[i].value` and `totalFee` and the invariant that the timing of protocol fee conversion must not be selectable by an unrelated party, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest is permissionless and drives the whole fee split)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: the timing of protocol fee conversion must not be selectable by an unrelated party; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract is holding WOM collected as a protocol fee that has not yet been split, call `harvest(address _lpToken)`, and assert `feeInfos[i].value` equals `totalFee` and that no account can withdraw more than it put in.

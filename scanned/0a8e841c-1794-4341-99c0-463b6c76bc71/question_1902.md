# Q1902: WombatStaking.harvest - harvest is permissionless and drives the whole fee split

## Question
wombat/WombatStaking.sol: harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. With _lpToken and the timing of every harvest-driven fee split under attacker control and a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, can an unprivileged caller sequence `harvest(address _lpToken)` so that `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` no longer reconcile, violating the invariant that the timing of protocol fee conversion must not be selectable by an unrelated party and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest is permissionless and drives the whole fee split)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Precondition: a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert.
- Invariant to test: the timing of protocol fee conversion must not be selectable by an unrelated party; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `harvest(address _lpToken)`: constrain the setup so that a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, fuzz the attacker inputs (_lpToken and the timing of every harvest-driven fee split), and assert after every call that the timing of protocol fee conversion must not be selectable by an unrelated party.

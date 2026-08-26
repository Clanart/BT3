# Q0256: WombatStaking.harvest - harvest is permissionless and drives the whole fee split

## Question
In wombat/WombatStaking.sol, harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Starting from a state where the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, can an unprivileged EOA use `harvest(address _lpToken)` to leave `IERC20(wom).balanceOf(address(this))` inconsistent with `totalConverted in mWOM`, violating the invariant that the timing of protocol fee conversion must not be selectable by an unrelated party and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest is permissionless and drives the whole fee split)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Precondition: the contract is holding WOM that mWOM._convert has just transferred in but not yet locked.
- Invariant to test: the timing of protocol fee conversion must not be selectable by an unrelated party; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_lpToken and the timing of every harvest-driven fee split) under the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, asserting on every row that the timing of protocol fee conversion must not be selectable by an unrelated party.

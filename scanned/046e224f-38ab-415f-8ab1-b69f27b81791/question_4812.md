# Q4812: WombatStaking.harvest - harvest is permissionless and drives the whole fee split

## Question
wombat/WombatStaking.sol: harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Under the attacker deposits and withdraws through the same helper inside one transaction, is there an unprivileged sequence of `harvest(address _lpToken)` that leaves `IERC20(wom).balanceOf(address(this))` unreconciled with `totalConverted in mWOM`, violates the invariant that the timing of protocol fee conversion must not be selectable by an unrelated party, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest is permissionless and drives the whole fee split)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Precondition: the attacker deposits and withdraws through the same helper inside one transaction.
- Invariant to test: the timing of protocol fee conversion must not be selectable by an unrelated party; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_lpToken and the timing of every harvest-driven fee split) under the attacker deposits and withdraws through the same helper inside one transaction, asserting on every row that the timing of protocol fee conversion must not be selectable by an unrelated party.

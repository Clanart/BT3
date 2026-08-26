# Q5313: WombatStaking.harvest - harvest is permissionless and drives the whole fee split

## Question
wombat/WombatStaking.sol - harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Can an unprivileged attacker controlling _lpToken and the timing of every harvest-driven fee split, under the bonus reward token registered for the asset is also one of the fee currencies, exploit this through `harvest(address _lpToken)` to break the reconciliation between `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` and the invariant that the timing of protocol fee conversion must not be selectable by an unrelated party, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest is permissionless and drives the whole fee split)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Precondition: the bonus reward token registered for the asset is also one of the fee currencies.
- Invariant to test: the timing of protocol fee conversion must not be selectable by an unrelated party; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the bonus reward token registered for the asset is also one of the fee currencies, have the attacker run `harvest(address _lpToken)`, then assert the victim's claimable value and the `womRewards measured by balance delta` versus `the amount queued into poolInfo.rewarder` relation are unchanged by the attacker's transaction.

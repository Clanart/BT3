# Q2569: WombatStaking.harvest - harvest is permissionless and drives the whole fee split

## Question
wombat/WombatStaking.sol - harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Can an unprivileged attacker controlling _lpToken and the timing of every harvest-driven fee split, under smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, exploit this through `harvest(address _lpToken)` to break the reconciliation between `isPoolFeeFree[_lpToken]` and `feeInfos.length` and the invariant that the timing of protocol fee conversion must not be selectable by an unrelated party, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest is permissionless and drives the whole fee split)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: harvest(address) is callable by anyone for any active pool and runs _toMasterWomAndSendReward, which performs the full feeInfos loop, the smartConvert leg and every queueNewRewards, so an attacker controls the timing of every fee conversion. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: the timing of protocol fee conversion must not be selectable by an unrelated party; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, have the attacker run `harvest(address _lpToken)`, then assert the victim's claimable value and the `isPoolFeeFree[_lpToken]` versus `feeInfos.length` relation are unchanged by the attacker's transaction.

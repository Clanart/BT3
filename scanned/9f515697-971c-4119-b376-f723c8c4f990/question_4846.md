# Q4846: WombatStaking.harvest - reward amounts measured by balance delta across the same fee loop

## Question
wombat/WombatStaking.sol - _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Can an unprivileged attacker controlling _lpToken and the timing of every harvest-driven fee split, under the attacker deposits and withdraws through the same helper inside one transaction, exploit this through `harvest(address _lpToken)` to break the reconciliation between `isPoolFeeFree[_lpToken]` and `feeInfos.length` and the invariant that harvested reward measurement must be isolated from the fee movements that consume it, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: the attacker deposits and withdraws through the same helper inside one transaction.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker deposits and withdraws through the same helper inside one transaction, call `harvest(address _lpToken)`, and assert `isPoolFeeFree[_lpToken]` equals `feeInfos.length` and that no account can withdraw more than it put in.

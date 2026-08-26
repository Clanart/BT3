# Q4107: WombatStaking.harvest - reward amounts measured by balance delta across the same fee loop

## Question
wombat/WombatStaking.sol: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. With _lpToken and the timing of every harvest-driven fee split under attacker control and several feeInfos entries are active at once and the harvested amount is small, can an unprivileged caller sequence `harvest(address _lpToken)` so that `feeInfos[i].value` and `totalFee` no longer reconcile, violating the invariant that harvested reward measurement must be isolated from the fee movements that consume it and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `harvest(address _lpToken)`: constrain the setup so that several feeInfos entries are active at once and the harvested amount is small, fuzz the attacker inputs (_lpToken and the timing of every harvest-driven fee split), and assert after every call that harvested reward measurement must be isolated from the fee movements that consume it.

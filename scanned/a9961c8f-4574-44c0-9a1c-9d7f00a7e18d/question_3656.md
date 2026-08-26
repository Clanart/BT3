# Q3656: WombatStaking.harvest - reward amounts measured by balance delta across the same fee loop

## Question
Note that in wombat/WombatStaking.sol, _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Can an attacker holding only tokens bought on market reach it via `harvest(address _lpToken)` under the pool is marked isPoolFeeFree so the fee loop is skipped entirely and force `IERC20(wom).balanceOf(address(this))` apart from `totalConverted in mWOM`, breaking the invariant that harvested reward measurement must be isolated from the fee movements that consume it for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the pool is marked isPoolFeeFree so the fee loop is skipped entirely, have the attacker run `harvest(address _lpToken)`, then assert the victim's claimable value and the `IERC20(wom).balanceOf(address(this))` versus `totalConverted in mWOM` relation are unchanged by the attacker's transaction.

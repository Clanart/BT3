# Q0349: WombatStaking.harvest - reward amounts measured by balance delta across the same fee loop

## Question
In wombat/WombatStaking.sol, _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Starting from a state where the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, can an unprivileged EOA use `harvest(address _lpToken)` to leave `isPoolFeeFree[_lpToken]` inconsistent with `feeInfos.length`, violating the invariant that harvested reward measurement must be isolated from the fee movements that consume it and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: the contract is holding WOM that mWOM._convert has just transferred in but not yet locked.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `harvest(address _lpToken)` sequence atomically under the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, asserting at the end that `isPoolFeeFree[_lpToken]` still equals `feeInfos.length` and the PoC's balance delta is non-positive.

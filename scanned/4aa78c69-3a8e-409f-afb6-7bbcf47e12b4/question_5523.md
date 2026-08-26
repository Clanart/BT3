# Q5523: WombatStaking.harvest - reward amounts measured by balance delta across the same fee loop

## Question
wombat/WombatStaking.sol: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. With _lpToken and the timing of every harvest-driven fee split under attacker control and the veWOM contract leaves a non-zero allowance after mint, can an unprivileged caller sequence `harvest(address _lpToken)` so that `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid` no longer reconcile, violating the invariant that harvested reward measurement must be isolated from the fee movements that consume it and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: the veWOM contract leaves a non-zero allowance after mint.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `harvest(address _lpToken)`: constrain the setup so that the veWOM contract leaves a non-zero allowance after mint, fuzz the attacker inputs (_lpToken and the timing of every harvest-driven fee split), and assert after every call that harvested reward measurement must be isolated from the fee movements that consume it.

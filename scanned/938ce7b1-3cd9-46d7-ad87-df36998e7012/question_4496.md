# Q4496: WombatStaking.harvest - reward amounts measured by balance delta across the same fee loop

## Question
Note that in wombat/WombatStaking.sol, _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Can an attacker holding only tokens bought on market reach it via `harvest(address _lpToken)` under the deposit token for the pool is wBNB and the helper arrived through depositNative and force `womRewards measured by balance delta` apart from `the amount queued into poolInfo.rewarder`, breaking the invariant that harvested reward measurement must be isolated from the fee movements that consume it for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the deposit token for the pool is wBNB and the helper arrived through depositNative, snapshot `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder`, run the attacker's `harvest(address _lpToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

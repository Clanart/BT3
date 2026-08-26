# Q5494: WombatPoolHelper.harvest - harvest is permissionless and drives the fee and conversion legs

## Question
In wombat/WombatPoolHelper.sol, harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Can an unprivileged attacker reach this through `harvest()` while the attacker deposits and withdraws through the helper inside one transaction, and drive `this.balance(msg.sender)` out of agreement with `lockedAmount[msg.sender]` - breaking the invariant that the timing of fee conversion for a pool must not be selectable by an unrelated party - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `harvest()` (mechanism: harvest is permissionless and drives the fee and conversion legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the pool's rewards are harvested and fee-split
- Exploit idea: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: the timing of fee conversion for a pool must not be selectable by an unrelated party; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker deposits and withdraws through the helper inside one transaction, snapshot `this.balance(msg.sender)` and `lockedAmount[msg.sender]`, run the attacker's `harvest()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

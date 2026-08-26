# Q2435: WombatPoolHelperV2.harvest - harvest is permissionless and drives the fee and conversion legs

## Question
Note that in wombat/WombatPoolHelperV2.sol, harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Can an attacker holding only tokens bought on market reach it via `harvest()` under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction and force `this.balance(msg.sender)` apart from `lockedAmount[msg.sender]`, breaking the invariant that the timing of fee conversion for a pool must not be selectable by an unrelated party for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `harvest()` (mechanism: harvest is permissionless and drives the fee and conversion legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the harvest timing for the whole pool
- Exploit idea: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: the timing of fee conversion for a pool must not be selectable by an unrelated party; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, snapshot `this.balance(msg.sender)` and `lockedAmount[msg.sender]`, run the attacker's `harvest()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

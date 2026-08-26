# Q5155: WombatPoolHelperV2.harvest - harvest is permissionless and drives the fee and conversion legs

## Question
Note that in wombat/WombatPoolHelperV2.sol, harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Can an attacker holding only tokens bought on market reach it via `harvest()` under the attacker has moved the wom/mWom Wombat pool immediately before calling and force `this.balance(msg.sender)` apart from `lockedAmount[msg.sender]`, breaking the invariant that the timing of fee conversion for a pool must not be selectable by an unrelated party for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `harvest()` (mechanism: harvest is permissionless and drives the fee and conversion legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the harvest timing for the whole pool
- Exploit idea: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: the timing of fee conversion for a pool must not be selectable by an unrelated party; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker has moved the wom/mWom Wombat pool immediately before calling, then assert `this.balance(msg.sender)` and `lockedAmount[msg.sender]` end identical in both runs.

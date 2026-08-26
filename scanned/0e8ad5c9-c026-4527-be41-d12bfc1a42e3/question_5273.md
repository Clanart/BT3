# Q5273: WombatPoolHelper.harvest - harvest is permissionless and drives the fee and conversion legs

## Question
In wombat/WombatPoolHelper.sol, harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Starting from a state where the attacker has moved the wom/mWom Wombat pool immediately before calling, can an unprivileged EOA use `harvest()` to leave `_liquidity burned via burnReceiptToken` inconsistent with `the deposit-token balance delta paid out by WombatStaking.withdraw`, violating the invariant that the timing of fee conversion for a pool must not be selectable by an unrelated party and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `harvest()` (mechanism: harvest is permissionless and drives the fee and conversion legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the pool's rewards are harvested and fee-split
- Exploit idea: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: the timing of fee conversion for a pool must not be selectable by an unrelated party; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `harvest()`: constrain the setup so that the attacker has moved the wom/mWom Wombat pool immediately before calling, fuzz the attacker inputs (the exact block at which the pool's rewards are harvested and fee-split), and assert after every call that the timing of fee conversion for a pool must not be selectable by an unrelated party.

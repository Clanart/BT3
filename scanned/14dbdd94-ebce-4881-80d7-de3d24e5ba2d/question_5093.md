# Q5093: AnkrBNBPoolHelper.harvest - harvest is permissionless and drives the fee and conversion legs

## Question
wombat/AnkrBNBPoolHelper.sol: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Under the attacker has moved the wom/mWom Wombat pool immediately before calling, is there an unprivileged sequence of `harvest()` that leaves `_liquidity burned via burnReceiptToken` unreconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`, violates the invariant that the timing of fee conversion for a pool must not be selectable by an unrelated party, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `harvest()` (mechanism: harvest is permissionless and drives the fee and conversion legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the harvest timing for the whole pool
- Exploit idea: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: the timing of fee conversion for a pool must not be selectable by an unrelated party; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `harvest()` sequence atomically under the attacker has moved the wom/mWom Wombat pool immediately before calling, asserting at the end that `_liquidity burned via burnReceiptToken` still equals `the deposit-token balance delta paid out by WombatStaking.withdraw` and the PoC's balance delta is non-positive.

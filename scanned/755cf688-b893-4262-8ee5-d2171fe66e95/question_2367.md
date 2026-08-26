# Q2367: AnkrBNBPoolHelper.harvest - harvest is permissionless and drives the fee and conversion legs

## Question
wombat/AnkrBNBPoolHelper.sol: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. With the harvest timing for the whole pool under attacker control and the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, can an unprivileged caller sequence `harvest()` so that `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` no longer reconcile, violating the invariant that the timing of fee conversion for a pool must not be selectable by an unrelated party and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `harvest()` (mechanism: harvest is permissionless and drives the fee and conversion legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the harvest timing for the whole pool
- Exploit idea: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: the timing of fee conversion for a pool must not be selectable by an unrelated party; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `harvest()`: constrain the setup so that the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, fuzz the attacker inputs (the harvest timing for the whole pool), and assert after every call that the timing of fee conversion for a pool must not be selectable by an unrelated party.

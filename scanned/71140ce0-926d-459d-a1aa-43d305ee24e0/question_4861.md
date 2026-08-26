# Q4861: WombatPoolHelperV2.harvest - harvest is permissionless and drives the fee and conversion legs

## Question
In wombat/WombatPoolHelperV2.sol, harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Can an unprivileged attacker reach this through `harvest()` while an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, and drive `_liquidity burned via burnReceiptToken` out of agreement with `the deposit-token balance delta paid out by WombatStaking.withdraw` - breaking the invariant that the timing of fee conversion for a pool must not be selectable by an unrelated party - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `harvest()` (mechanism: harvest is permissionless and drives the fee and conversion legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the harvest timing for the whole pool
- Exploit idea: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Precondition: an active mWOM-flagged fee entry routes the harvest through SmartWomConvert.
- Invariant to test: the timing of fee conversion for a pool must not be selectable by an unrelated party; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `harvest()`: constrain the setup so that an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, fuzz the attacker inputs (the harvest timing for the whole pool), and assert after every call that the timing of fee conversion for a pool must not be selectable by an unrelated party.

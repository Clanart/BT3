# Q0879: WombatPoolHelper.harvest - harvest is permissionless and drives the fee and conversion legs

## Question
wombat/WombatPoolHelper.sol: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. With the exact block at which the pool's rewards are harvested and fee-split under attacker control and the pool's deposit token is wBNB and the caller arrived through depositNative, can an unprivileged caller sequence `harvest()` so that `pid cached at construction` and `pools[lpToken].pid in WombatStaking` no longer reconcile, violating the invariant that the timing of fee conversion for a pool must not be selectable by an unrelated party and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `harvest()` (mechanism: harvest is permissionless and drives the fee and conversion legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the pool's rewards are harvested and fee-split
- Exploit idea: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: the timing of fee conversion for a pool must not be selectable by an unrelated party; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool's deposit token is wBNB and the caller arrived through depositNative, then assert `pid cached at construction` and `pools[lpToken].pid in WombatStaking` end identical in both runs.

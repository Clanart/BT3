# Q3580: WombatPoolHelperV2.harvest - harvest is permissionless and drives the fee and conversion legs

## Question
wombat/WombatPoolHelperV2.sol: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Under a residual stakingToken balance from an earlier rounding sits on the helper, is there an unprivileged sequence of `harvest()` that leaves `_minimumLiquidity supplied by the caller` unreconciled with `the LP actually minted by the Wombat pool`, violates the invariant that the timing of fee conversion for a pool must not be selectable by an unrelated party, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `harvest()` (mechanism: harvest is permissionless and drives the fee and conversion legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the harvest timing for the whole pool
- Exploit idea: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: the timing of fee conversion for a pool must not be selectable by an unrelated party; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the harvest timing for the whole pool) under a residual stakingToken balance from an earlier rounding sits on the helper, asserting on every row that the timing of fee conversion for a pool must not be selectable by an unrelated party.

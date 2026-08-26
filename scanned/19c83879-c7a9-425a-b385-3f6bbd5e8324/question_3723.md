# Q3723: WombatPoolHelper.harvest - harvest is permissionless and drives the fee and conversion legs

## Question
In wombat/WombatPoolHelper.sol, harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Does `harvest()` let an unprivileged caller exploit that under a residual stakingToken balance from an earlier rounding sits on the helper, so that `IERC20(stakingToken).totalSupply()` diverges from `the MasterWombat staked balance for pid`, the invariant that the timing of fee conversion for a pool must not be selectable by an unrelated party is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `harvest()` (mechanism: harvest is permissionless and drives the fee and conversion legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the pool's rewards are harvested and fee-split
- Exploit idea: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: the timing of fee conversion for a pool must not be selectable by an unrelated party; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `harvest()`: constrain the setup so that a residual stakingToken balance from an earlier rounding sits on the helper, fuzz the attacker inputs (the exact block at which the pool's rewards are harvested and fee-split), and assert after every call that the timing of fee conversion for a pool must not be selectable by an unrelated party.

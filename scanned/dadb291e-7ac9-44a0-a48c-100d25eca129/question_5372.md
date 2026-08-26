# Q5372: WombatPoolHelperV2.harvest - harvest is permissionless and drives the fee and conversion legs

## Question
wombat/WombatPoolHelperV2.sol: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Under the attacker deposits and withdraws through the helper inside one transaction, is there an unprivileged sequence of `harvest()` that leaves `IERC20(stakingToken).totalSupply()` unreconciled with `the MasterWombat staked balance for pid`, violates the invariant that the timing of fee conversion for a pool must not be selectable by an unrelated party, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `harvest()` (mechanism: harvest is permissionless and drives the fee and conversion legs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the harvest timing for the whole pool
- Exploit idea: harvest() forwards to IWombatStaking(wombatStaking).harvest(lpToken) with no caller restriction, so any address decides when the pool's WOM and bonus rewards are harvested, fee-split and routed through the spot-priced smart convert. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: the timing of fee conversion for a pool must not be selectable by an unrelated party; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `harvest()` sequence atomically under the attacker deposits and withdraws through the helper inside one transaction, asserting at the end that `IERC20(stakingToken).totalSupply()` still equals `the MasterWombat staked balance for pid` and the PoC's balance delta is non-positive.

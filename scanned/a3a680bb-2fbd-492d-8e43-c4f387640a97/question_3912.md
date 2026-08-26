# Q3912: WombatStaking.withdraw - bonus reward before-balances captured before an attacker-timed transfer

## Question
wombat/WombatStaking.sol: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. With _liquidity and _minAmount, forwarded verbatim from the helper's withdraw under attacker control and the pool is marked isPoolFeeFree so the fee loop is skipped entirely, can an unprivileged caller sequence `withdraw(address,uint256,uint256,address) via a pool helper` so that `isPoolFeeFree[_lpToken]` and `feeInfos.length` no longer reconcile, violating the invariant that harvest accounting must not credit tokens that were not produced by the harvest and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool is marked isPoolFeeFree so the fee loop is skipped entirely, then assert `isPoolFeeFree[_lpToken]` and `feeInfos.length` end identical in both runs.

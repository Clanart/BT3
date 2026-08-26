# Q5138: WombatStaking.harvest - bonus reward before-balances captured before an attacker-timed transfer

## Question
wombat/WombatStaking.sol: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. With _lpToken and the timing of every harvest-driven fee split under attacker control and a large honest deposit is pending in the mempool for the same pool, can an unprivileged caller sequence `harvest(address _lpToken)` so that `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` and `_liquidity burned from the receipt token` no longer reconcile, violating the invariant that harvest accounting must not credit tokens that were not produced by the harvest and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: a large honest deposit is pending in the mempool for the same pool.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large honest deposit is pending in the mempool for the same pool, call `harvest(address _lpToken)`, and assert `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` equals `_liquidity burned from the receipt token` and that no account can withdraw more than it put in.

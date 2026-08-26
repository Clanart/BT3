# Q1269: WombatStaking.harvest - bonus reward before-balances captured before an attacker-timed transfer

## Question
wombat/WombatStaking.sol - _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Can an unprivileged attacker controlling _lpToken and the timing of every harvest-driven fee split, under the contract is holding WOM collected as a protocol fee that has not yet been split, exploit this through `harvest(address _lpToken)` to break the reconciliation between `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` and `_liquidity burned from the receipt token` and the invariant that harvest accounting must not credit tokens that were not produced by the harvest, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `harvest(address _lpToken)`: constrain the setup so that the contract is holding WOM collected as a protocol fee that has not yet been split, fuzz the attacker inputs (_lpToken and the timing of every harvest-driven fee split), and assert after every call that harvest accounting must not credit tokens that were not produced by the harvest.

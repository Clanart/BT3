# Q4857: WombatStaking.harvest - bonus reward before-balances captured before an attacker-timed transfer

## Question
In wombat/WombatStaking.sol, _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Can an unprivileged attacker reach this through `harvest(address _lpToken)` while the attacker deposits and withdraws through the same helper inside one transaction, and drive `IERC20(poolInfo.lpAddress).balanceOf(address(this))` out of agreement with `lpReceived credited by IMintableERC20(receiptToken).mint` - breaking the invariant that harvest accounting must not credit tokens that were not produced by the harvest - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: the attacker deposits and withdraws through the same helper inside one transaction.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_lpToken and the timing of every harvest-driven fee split) under the attacker deposits and withdraws through the same helper inside one transaction, asserting on every row that harvest accounting must not credit tokens that were not produced by the harvest.

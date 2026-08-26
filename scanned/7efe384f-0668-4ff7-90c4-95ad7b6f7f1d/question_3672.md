# Q3672: WombatStaking.harvest - bonus reward before-balances captured before an attacker-timed transfer

## Question
wombat/WombatStaking.sol: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Under the pool is marked isPoolFeeFree so the fee loop is skipped entirely, is there an unprivileged sequence of `harvest(address _lpToken)` that leaves `feeInfos[i].value` unreconciled with `totalFee`, violates the invariant that harvest accounting must not credit tokens that were not produced by the harvest, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool is marked isPoolFeeFree so the fee loop is skipped entirely, call `harvest(address _lpToken)`, and assert `feeInfos[i].value` equals `totalFee` and that no account can withdraw more than it put in.

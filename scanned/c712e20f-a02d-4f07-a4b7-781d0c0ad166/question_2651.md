# Q2651: WombatStaking.harvest - bonus reward before-balances captured before an attacker-timed transfer

## Question
Consider wombat/WombatStaking.sol, where _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Assuming smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, can an unprivileged attacker turn this into a divergence between `totalAccumulated in mWOM` and `veWom balance of WombatStaking` via `harvest(address _lpToken)`, breaking the invariant that harvest accounting must not credit tokens that were not produced by the harvest and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, snapshot `totalAccumulated in mWOM` and `veWom balance of WombatStaking`, run the attacker's `harvest(address _lpToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

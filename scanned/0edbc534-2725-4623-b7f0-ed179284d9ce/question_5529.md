# Q5529: WombatStaking.harvest - bonus reward before-balances captured before an attacker-timed transfer

## Question
In wombat/WombatStaking.sol, _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Can an unprivileged attacker reach this through `harvest(address _lpToken)` while the veWOM contract leaves a non-zero allowance after mint, and drive `totalAccumulated in mWOM` out of agreement with `veWom balance of WombatStaking` - breaking the invariant that harvest accounting must not credit tokens that were not produced by the harvest - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: the veWOM contract leaves a non-zero allowance after mint.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `harvest(address _lpToken)`: constrain the setup so that the veWOM contract leaves a non-zero allowance after mint, fuzz the attacker inputs (_lpToken and the timing of every harvest-driven fee split), and assert after every call that harvest accounting must not credit tokens that were not produced by the harvest.

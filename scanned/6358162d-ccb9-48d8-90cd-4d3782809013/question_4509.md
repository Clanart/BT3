# Q4509: WombatStaking.harvest - bonus reward before-balances captured before an attacker-timed transfer

## Question
wombat/WombatStaking.sol - _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Can an unprivileged attacker controlling _lpToken and the timing of every harvest-driven fee split, under the deposit token for the pool is wBNB and the helper arrived through depositNative, exploit this through `harvest(address _lpToken)` to break the reconciliation between `isPoolFeeFree[_lpToken]` and `feeInfos.length` and the invariant that harvest accounting must not credit tokens that were not produced by the harvest, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the deposit token for the pool is wBNB and the helper arrived through depositNative, snapshot `isPoolFeeFree[_lpToken]` and `feeInfos.length`, run the attacker's `harvest(address _lpToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

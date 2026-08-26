# Q5341: WombatStaking.harvest - bonus reward before-balances captured before an attacker-timed transfer

## Question
wombat/WombatStaking.sol - _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Can an unprivileged attacker controlling _lpToken and the timing of every harvest-driven fee split, under the bonus reward token registered for the asset is also one of the fee currencies, exploit this through `harvest(address _lpToken)` to break the reconciliation between `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid` and the invariant that harvest accounting must not credit tokens that were not produced by the harvest, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: the bonus reward token registered for the asset is also one of the fee currencies.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `harvest(address _lpToken)`: constrain the setup so that the bonus reward token registered for the asset is also one of the fee currencies, fuzz the attacker inputs (_lpToken and the timing of every harvest-driven fee split), and assert after every call that harvest accounting must not credit tokens that were not produced by the harvest.

# Q1665: WombatStaking.withdraw - bonus reward before-balances captured before an attacker-timed transfer

## Question
Consider wombat/WombatStaking.sol, where _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Assuming the contract is holding WOM collected as a protocol fee that has not yet been split, can an unprivileged attacker turn this into a divergence between `totalAccumulated in mWOM` and `veWom balance of WombatStaking` via `withdraw(address,uint256,uint256,address) via a pool helper`, breaking the invariant that harvest accounting must not credit tokens that were not produced by the harvest and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity and _minAmount, forwarded verbatim from the helper's withdraw) under the contract is holding WOM collected as a protocol fee that has not yet been split, asserting on every row that harvest accounting must not credit tokens that were not produced by the harvest.

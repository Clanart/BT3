# Q3443: WombatStaking.withdraw - bonus reward before-balances captured before an attacker-timed transfer

## Question
wombat/WombatStaking.sol - _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Can an unprivileged attacker controlling _liquidity and _minAmount, forwarded verbatim from the helper's withdraw, under the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, exploit this through `withdraw(address,uint256,uint256,address) via a pool helper` to break the reconciliation between `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` and the invariant that harvest accounting must not credit tokens that were not produced by the harvest, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `withdraw(address,uint256,uint256,address) via a pool helper`: constrain the setup so that the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, fuzz the attacker inputs (_liquidity and _minAmount, forwarded verbatim from the helper's withdraw), and assert after every call that harvest accounting must not credit tokens that were not produced by the harvest.

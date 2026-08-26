# Q5022: WombatStaking.withdraw - bonus reward before-balances captured before an attacker-timed transfer

## Question
wombat/WombatStaking.sol: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Under the attacker deposits and withdraws through the same helper inside one transaction, is there an unprivileged sequence of `withdraw(address,uint256,uint256,address) via a pool helper` that leaves `IMintableERC20(poolInfo.receiptToken).totalSupply()` unreconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`, violates the invariant that harvest accounting must not credit tokens that were not produced by the harvest, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: the attacker deposits and withdraws through the same helper inside one transaction.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker deposits and withdraws through the same helper inside one transaction, then assert `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid` end identical in both runs.

# Q0845: WombatStaking.withdraw - bonus reward before-balances captured before an attacker-timed transfer

## Question
Note that in wombat/WombatStaking.sol, _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Can an attacker holding only tokens bought on market reach it via `withdraw(address,uint256,uint256,address) via a pool helper` under the contract is holding WOM that mWOM._convert has just transferred in but not yet locked and force `IMintableERC20(poolInfo.receiptToken).totalSupply()` apart from `IMasterWombat(masterWombat) staked balance for poolInfo.pid`, breaking the invariant that harvest accounting must not credit tokens that were not produced by the harvest for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: the contract is holding WOM that mWOM._convert has just transferred in but not yet locked.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `withdraw(address,uint256,uint256,address) via a pool helper` sequence atomically under the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, asserting at the end that `IMintableERC20(poolInfo.receiptToken).totalSupply()` still equals `IMasterWombat(masterWombat) staked balance for poolInfo.pid` and the PoC's balance delta is non-positive.

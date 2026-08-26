# Q5194: WombatStaking.deposit - bonus reward before-balances captured before an attacker-timed transfer

## Question
Consider wombat/WombatStaking.sol, where _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Assuming a large honest deposit is pending in the mempool for the same pool, can an unprivileged attacker turn this into a divergence between `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid` via `deposit(address,uint256,uint256,address,address) via a pool helper`, breaking the invariant that harvest accounting must not credit tokens that were not produced by the harvest and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: a large honest deposit is pending in the mempool for the same pool.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large honest deposit is pending in the mempool for the same pool, call `deposit(address,uint256,uint256,address,address) via a pool helper`, and assert `IMintableERC20(poolInfo.receiptToken).totalSupply()` equals `IMasterWombat(masterWombat) staked balance for poolInfo.pid` and that no account can withdraw more than it put in.

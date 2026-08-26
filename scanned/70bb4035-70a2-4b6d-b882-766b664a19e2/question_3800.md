# Q3800: WombatStaking.deposit - bonus reward before-balances captured before an attacker-timed transfer

## Question
In wombat/WombatStaking.sol, _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Can an unprivileged attacker reach this through `deposit(address,uint256,uint256,address,address) via a pool helper` while the pool is marked isPoolFeeFree so the fee loop is skipped entirely, and drive `womRewards measured by balance delta` out of agreement with `the amount queued into poolInfo.rewarder` - breaking the invariant that harvest accounting must not credit tokens that were not produced by the harvest - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool is marked isPoolFeeFree so the fee loop is skipped entirely, then assert `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` end identical in both runs.

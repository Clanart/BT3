# Q4327: WombatStaking.withdraw - bonus reward before-balances captured before an attacker-timed transfer

## Question
In wombat/WombatStaking.sol, _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Can an unprivileged attacker reach this through `withdraw(address,uint256,uint256,address) via a pool helper` while several feeInfos entries are active at once and the harvested amount is small, and drive `IERC20(poolInfo.lpAddress).balanceOf(address(this))` out of agreement with `lpReceived credited by IMintableERC20(receiptToken).mint` - breaking the invariant that harvest accounting must not credit tokens that were not produced by the harvest - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity and _minAmount, forwarded verbatim from the helper's withdraw) under several feeInfos entries are active at once and the harvested amount is small, asserting on every row that harvest accounting must not credit tokens that were not produced by the harvest.

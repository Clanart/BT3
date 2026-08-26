# Q4692: WombatStaking.withdraw - bonus reward before-balances captured before an attacker-timed transfer

## Question
In wombat/WombatStaking.sol, _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Does `withdraw(address,uint256,uint256,address) via a pool helper` let an unprivileged caller exploit that under the deposit token for the pool is wBNB and the helper arrived through depositNative, so that `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` diverges from `_liquidity burned from the receipt token`, the invariant that harvest accounting must not credit tokens that were not produced by the harvest is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the deposit token for the pool is wBNB and the helper arrived through depositNative, call `withdraw(address,uint256,uint256,address) via a pool helper`, and assert `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` equals `_liquidity burned from the receipt token` and that no account can withdraw more than it put in.

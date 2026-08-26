# Q5445: WombatStaking.withdraw - bonus reward before-balances captured before an attacker-timed transfer

## Question
Note that in wombat/WombatStaking.sol, _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Can an attacker holding only tokens bought on market reach it via `withdraw(address,uint256,uint256,address) via a pool helper` under the bonus reward token registered for the asset is also one of the fee currencies and force `IERC20(wom).balanceOf(address(this))` apart from `totalConverted in mWOM`, breaking the invariant that harvest accounting must not credit tokens that were not produced by the harvest for High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: bonus reward before-balances captured before an attacker-timed transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _rewardBeforeBalances() snapshots balances immediately before the MasterWombat call, so a bonus token transferred into WombatStaking between the snapshot and the delta is credited as harvested reward and queued to the pool rewarder. Precondition: the bonus reward token registered for the asset is also one of the fee currencies.
- Invariant to test: harvest accounting must not credit tokens that were not produced by the harvest; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the bonus reward token registered for the asset is also one of the fee currencies, call `withdraw(address,uint256,uint256,address) via a pool helper`, and assert `IERC20(wom).balanceOf(address(this))` equals `totalConverted in mWOM` and that no account can withdraw more than it put in.

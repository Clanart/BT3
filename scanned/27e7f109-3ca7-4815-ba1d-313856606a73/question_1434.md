# Q1434: mWOM.incentiveDeposit - incentiveDeposit is reachable while the WOM leg is not converted

## Question
In wombat/mWOM.sol, incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Does `incentiveDeposit(uint256 _amount, bool _stake)` let an unprivileged caller exploit that under rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, so that `IERC20(this).totalSupply()` diverges from `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, the invariant that an incentive paid for committing value must only be paid once the value is actually committed is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit is reachable while the WOM leg is not converted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: an incentive paid for committing value must only be paid once the value is actually committed; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, call `incentiveDeposit(uint256 _amount, bool _stake)`, and assert `IERC20(this).totalSupply()` equals `IERC20(wom).balanceOf(wombatStaking) + veWom backing` and that no account can withdraw more than it put in.

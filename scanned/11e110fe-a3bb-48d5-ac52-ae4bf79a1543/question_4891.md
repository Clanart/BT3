# Q4891: mWOM.incentiveDeposit - incentiveDeposit is reachable while the WOM leg is not converted

## Question
Note that in wombat/mWOM.sol, incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, bool _stake)` under the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance and force `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` apart from `IERC20(mgp).balanceOf(address(this))`, breaking the invariant that an incentive paid for committing value must only be paid once the value is actually committed for Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit is reachable while the WOM leg is not converted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Precondition: the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance.
- Invariant to test: an incentive paid for committing value must only be paid once the value is actually committed; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, call `incentiveDeposit(uint256 _amount, bool _stake)`, and assert `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` equals `IERC20(mgp).balanceOf(address(this))` and that no account can withdraw more than it put in.

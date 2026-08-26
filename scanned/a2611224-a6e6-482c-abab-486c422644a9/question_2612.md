# Q2612: mWOM.incentiveDeposit - incentiveDeposit is reachable while the WOM leg is not converted

## Question
wombat/mWOM.sol: incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Under wombatStaking is holding WOM from an earlier deposit that has not been locked, is there an unprivileged sequence of `incentiveDeposit(uint256 _amount, bool _stake)` that leaves `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` unreconciled with `IERC20(mgp).balanceOf(address(this))`, violates the invariant that an incentive paid for committing value must only be paid once the value is actually committed, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit is reachable while the WOM leg is not converted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Precondition: wombatStaking is holding WOM from an earlier deposit that has not been locked.
- Invariant to test: an incentive paid for committing value must only be paid once the value is actually committed; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish wombatStaking is holding WOM from an earlier deposit that has not been locked, have the attacker run `incentiveDeposit(uint256 _amount, bool _stake)`, then assert the victim's claimable value and the `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.

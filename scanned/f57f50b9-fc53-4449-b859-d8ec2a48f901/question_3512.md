# Q3512: mWOM.incentiveDeposit - incentiveDeposit is reachable while the WOM leg is not converted

## Question
In wombat/mWOM.sol, incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Starting from a state where the veWOM mint returns less than the WOM supplied because of the lockDays curve, can an unprivileged EOA use `incentiveDeposit(uint256 _amount, bool _stake)` to leave `IERC20(wom).balanceOf(address(this))` inconsistent with `totalConverted`, violating the invariant that an incentive paid for committing value must only be paid once the value is actually committed and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit is reachable while the WOM leg is not converted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Precondition: the veWOM mint returns less than the WOM supplied because of the lockDays curve.
- Invariant to test: an incentive paid for committing value must only be paid once the value is actually committed; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, bool _stake)` sequence atomically under the veWOM mint returns less than the WOM supplied because of the lockDays curve, asserting at the end that `IERC20(wom).balanceOf(address(this))` still equals `totalConverted` and the PoC's balance delta is non-positive.

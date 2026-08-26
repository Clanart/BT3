# Q2354: ArbWomUp3.incentiveDeposit - the mode two doubling is applied after the balance cap

## Question
Consider wombat/ArbWomUp3.sol, where getRewardAmount() already clamps its result to IERC20(mgp).balanceOf(address(this)), and incentiveDeposit then multiplies by two when _mode is 2, so the figure passed to lockFor can exceed the MGP the contract holds. Assuming the caller's mWomSV locked balance is zero so the correction is at its bottom bracket, can an unprivileged attacker turn this into a divergence between `rewardToSend` and `IERC20(mgp).balanceOf(address(this))` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that a bonus multiplier must be applied before, not after, the check that the contract can pay and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the mode two doubling is applied after the balance cap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: getRewardAmount() already clamps its result to IERC20(mgp).balanceOf(address(this)), and incentiveDeposit then multiplies by two when _mode is 2, so the figure passed to lockFor can exceed the MGP the contract holds. Precondition: the caller's mWomSV locked balance is zero so the correction is at its bottom bracket.
- Invariant to test: a bonus multiplier must be applied before, not after, the check that the contract can pay; concretely, `rewardToSend` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`: constrain the setup so that the caller's mWomSV locked balance is zero so the correction is at its bottom bracket, fuzz the attacker inputs (_amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer), and assert after every call that a bonus multiplier must be applied before, not after, the check that the contract can pay.

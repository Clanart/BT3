# Q0430: ArbWomUp3.incentiveDeposit - the mode two doubling is applied after the balance cap

## Question
Note that in wombat/ArbWomUp3.sol, getRewardAmount() already clamps its result to IERC20(mgp).balanceOf(address(this)), and incentiveDeposit then multiplies by two when _mode is 2, so the figure passed to lockFor can exceed the MGP the contract holds. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under the caller sets _mode to 2 so the doubling applies and force `rewardToSend` apart from `IERC20(mgp).balanceOf(address(this))`, breaking the invariant that a bonus multiplier must be applied before, not after, the check that the contract can pay for Critical - Protocol insolvency?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the mode two doubling is applied after the balance cap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: getRewardAmount() already clamps its result to IERC20(mgp).balanceOf(address(this)), and incentiveDeposit then multiplies by two when _mode is 2, so the figure passed to lockFor can exceed the MGP the contract holds. Precondition: the caller sets _mode to 2 so the doubling applies.
- Invariant to test: a bonus multiplier must be applied before, not after, the check that the contract can pay; concretely, `rewardToSend` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence atomically under the caller sets _mode to 2 so the doubling applies, asserting at the end that `rewardToSend` still equals `IERC20(mgp).balanceOf(address(this))` and the PoC's balance delta is non-positive.

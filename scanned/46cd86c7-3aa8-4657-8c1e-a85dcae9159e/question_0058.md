# Q0058: ArbWomUp3.incentiveDeposit - the mode two doubling is applied after the balance cap

## Question
Consider wombat/ArbWomUp3.sol, where getRewardAmount() already clamps its result to IERC20(mgp).balanceOf(address(this)), and incentiveDeposit then multiplies by two when _mode is 2, so the figure passed to lockFor can exceed the MGP the contract holds. Assuming the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block, can an unprivileged attacker turn this into a divergence between `rewardToSend after the _mode == 2 doubling` and `the mgpleft cap applied inside getRewardAmount` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that a bonus multiplier must be applied before, not after, the check that the contract can pay and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the mode two doubling is applied after the balance cap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: getRewardAmount() already clamps its result to IERC20(mgp).balanceOf(address(this)), and incentiveDeposit then multiplies by two when _mode is 2, so the figure passed to lockFor can exceed the MGP the contract holds. Precondition: the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block.
- Invariant to test: a bonus multiplier must be applied before, not after, the check that the contract can pay; concretely, `rewardToSend after the _mode == 2 doubling` must stay reconciled with `the mgpleft cap applied inside getRewardAmount`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`: constrain the setup so that the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block, fuzz the attacker inputs (_amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer), and assert after every call that a bonus multiplier must be applied before, not after, the check that the contract can pay.

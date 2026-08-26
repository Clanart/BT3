# Q1802: ArbWomUp3.incentiveDeposit - the mode two doubling is applied after the balance cap

## Question
Consider wombat/ArbWomUp3.sol, where getRewardAmount() already clamps its result to IERC20(mgp).balanceOf(address(this)), and incentiveDeposit then multiplies by two when _mode is 2, so the figure passed to lockFor can exceed the MGP the contract holds. Assuming the caller sandwiches the wom/mWom Wombat pool around the transaction, can an unprivileged attacker turn this into a divergence between `mWomSV.getUserTotalLocked(account) read by getRewardAmount` and `the same read inside calDoubledCounted` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that a bonus multiplier must be applied before, not after, the check that the contract can pay and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the mode two doubling is applied after the balance cap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: getRewardAmount() already clamps its result to IERC20(mgp).balanceOf(address(this)), and incentiveDeposit then multiplies by two when _mode is 2, so the figure passed to lockFor can exceed the MGP the contract holds. Precondition: the caller sandwiches the wom/mWom Wombat pool around the transaction.
- Invariant to test: a bonus multiplier must be applied before, not after, the check that the contract can pay; concretely, `mWomSV.getUserTotalLocked(account) read by getRewardAmount` must stay reconciled with `the same read inside calDoubledCounted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller sandwiches the wom/mWom Wombat pool around the transaction, call `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, and assert `mWomSV.getUserTotalLocked(account) read by getRewardAmount` equals `the same read inside calDoubledCounted` and that no account can withdraw more than it put in.

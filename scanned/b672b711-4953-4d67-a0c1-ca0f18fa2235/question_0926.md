# Q0926: ArbWomUp3.incentiveDeposit - the locked amount is taken from the whole contract balance

## Question
Consider wombat/ArbWomUp3.sol, where _deposit() mode 2 reads mWomBal = IERC20(mWom).balanceOf(address(this)) after the convert and locks that entire balance for _account, so any mWOM stranded on the contract by an earlier rounding or partial fill is handed to this caller. Assuming the caller sets _mode to a value other than 1 or 2, can an unprivileged attacker turn this into a divergence between `rewardToSend after the _mode == 2 doubling` and `the mgpleft cap applied inside getRewardAmount` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that a caller must only be credited with the value their own transaction produced and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the locked amount is taken from the whole contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() mode 2 reads mWomBal = IERC20(mWom).balanceOf(address(this)) after the convert and locks that entire balance for _account, so any mWOM stranded on the contract by an earlier rounding or partial fill is handed to this caller. Precondition: the caller sets _mode to a value other than 1 or 2.
- Invariant to test: a caller must only be credited with the value their own transaction produced; concretely, `rewardToSend after the _mode == 2 doubling` must stay reconciled with `the mgpleft cap applied inside getRewardAmount`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller sets _mode to a value other than 1 or 2, call `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, and assert `rewardToSend after the _mode == 2 doubling` equals `the mgpleft cap applied inside getRewardAmount` and that no account can withdraw more than it put in.

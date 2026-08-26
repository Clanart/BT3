# Q2170: ArbWomUp3.incentiveDeposit - the locked amount is taken from the whole contract balance

## Question
Note that in wombat/ArbWomUp3.sol, _deposit() mode 2 reads mWomBal = IERC20(mWom).balanceOf(address(this)) after the convert and locks that entire balance for _account, so any mWOM stranded on the contract by an earlier rounding or partial fill is handed to this caller. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under a residual mWOM balance from an earlier call sits on the contract and force `IERC20(mWom).balanceOf(address(this))` apart from `the amount locked for _account in mode two`, breaking the invariant that a caller must only be credited with the value their own transaction produced for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the locked amount is taken from the whole contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() mode 2 reads mWomBal = IERC20(mWom).balanceOf(address(this)) after the convert and locks that entire balance for _account, so any mWOM stranded on the contract by an earlier rounding or partial fill is handed to this caller. Precondition: a residual mWOM balance from an earlier call sits on the contract.
- Invariant to test: a caller must only be credited with the value their own transaction produced; concretely, `IERC20(mWom).balanceOf(address(this))` must stay reconciled with `the amount locked for _account in mode two`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a residual mWOM balance from an earlier call sits on the contract, then assert `IERC20(mWom).balanceOf(address(this))` and `the amount locked for _account in mode two` end identical in both runs.

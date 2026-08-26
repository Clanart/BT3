# Q2703: ArbWomUp3.incentiveDeposit - the locked amount is taken from the whole contract balance

## Question
In wombat/ArbWomUp3.sol, _deposit() mode 2 reads mWomBal = IERC20(mWom).balanceOf(address(this)) after the convert and locks that entire balance for _account, so any mWOM stranded on the contract by an earlier rounding or partial fill is handed to this caller. Starting from a state where the caller crosses several tier boundaries in one deposit, can an unprivileged EOA use `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` to leave `rewardToSend after the _mode == 2 doubling` inconsistent with `the mgpleft cap applied inside getRewardAmount`, violating the invariant that a caller must only be credited with the value their own transaction produced and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the locked amount is taken from the whole contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() mode 2 reads mWomBal = IERC20(mWom).balanceOf(address(this)) after the convert and locks that entire balance for _account, so any mWOM stranded on the contract by an earlier rounding or partial fill is handed to this caller. Precondition: the caller crosses several tier boundaries in one deposit.
- Invariant to test: a caller must only be credited with the value their own transaction produced; concretely, `rewardToSend after the _mode == 2 doubling` must stay reconciled with `the mgpleft cap applied inside getRewardAmount`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer) under the caller crosses several tier boundaries in one deposit, asserting on every row that a caller must only be credited with the value their own transaction produced.

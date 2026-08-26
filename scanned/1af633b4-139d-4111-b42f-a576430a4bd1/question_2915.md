# Q2915: ArbWomUp3.incentiveDeposit - the internal convert leg passes a zero minimum received

## Question
Note that in wombat/ArbWomUp3.sol, _deposit() mode 2 calls convert with _minRec pinned to zero, so the swap has no slippage floor at all and the caller can sandwich their own transaction. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under the caller repeats the deposit from several addresses in one block and force `rewardToSend after the _mode == 2 doubling` apart from `the mgpleft cap applied inside getRewardAmount`, breaking the invariant that every swap of protocol or user value must carry a slippage floor for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the internal convert leg passes a zero minimum received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() mode 2 calls convert with _minRec pinned to zero, so the swap has no slippage floor at all and the caller can sandwich their own transaction. Precondition: the caller repeats the deposit from several addresses in one block.
- Invariant to test: every swap of protocol or user value must carry a slippage floor; concretely, `rewardToSend after the _mode == 2 doubling` must stay reconciled with `the mgpleft cap applied inside getRewardAmount`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller repeats the deposit from several addresses in one block, call `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, and assert `rewardToSend after the _mode == 2 doubling` equals `the mgpleft cap applied inside getRewardAmount` and that no account can withdraw more than it put in.

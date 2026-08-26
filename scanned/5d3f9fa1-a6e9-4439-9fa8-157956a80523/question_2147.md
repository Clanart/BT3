# Q2147: ArbWomUp3.incentiveDeposit - the internal convert leg passes a zero minimum received

## Question
Note that in wombat/ArbWomUp3.sol, _deposit() mode 2 calls convert with _minRec pinned to zero, so the swap has no slippage floor at all and the caller can sandwich their own transaction. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under a residual mWOM balance from an earlier call sits on the contract and force `_convertRatio supplied by the caller` apart from `the buyback leg inside SmartWomConvert`, breaking the invariant that every swap of protocol or user value must carry a slippage floor for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the internal convert leg passes a zero minimum received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() mode 2 calls convert with _minRec pinned to zero, so the swap has no slippage floor at all and the caller can sandwich their own transaction. Precondition: a residual mWOM balance from an earlier call sits on the contract.
- Invariant to test: every swap of protocol or user value must carry a slippage floor; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the buyback leg inside SmartWomConvert`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up a residual mWOM balance from an earlier call sits on the contract, snapshot `_convertRatio supplied by the caller` and `the buyback leg inside SmartWomConvert`, run the attacker's `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

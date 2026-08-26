# Q1871: ArbWomUp3.incentiveDeposit - the internal convert leg passes a zero minimum received

## Question
Consider wombat/ArbWomUp3.sol, where _deposit() mode 2 calls convert with _minRec pinned to zero, so the swap has no slippage floor at all and the caller can sandwich their own transaction. Assuming the caller sandwiches the wom/mWom Wombat pool around the transaction, can an unprivileged attacker turn this into a divergence between `bracketRewarded` and `calDoubledCounted(account)` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that every swap of protocol or user value must carry a slippage floor and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the internal convert leg passes a zero minimum received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() mode 2 calls convert with _minRec pinned to zero, so the swap has no slippage floor at all and the caller can sandwich their own transaction. Precondition: the caller sandwiches the wom/mWom Wombat pool around the transaction.
- Invariant to test: every swap of protocol or user value must carry a slippage floor; concretely, `bracketRewarded` must stay reconciled with `calDoubledCounted(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller sandwiches the wom/mWom Wombat pool around the transaction, then assert `bracketRewarded` and `calDoubledCounted(account)` end identical in both runs.

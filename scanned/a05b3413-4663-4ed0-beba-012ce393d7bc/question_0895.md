# Q0895: ArbWomUp3.incentiveDeposit - the internal convert leg passes a zero minimum received

## Question
Consider wombat/ArbWomUp3.sol, where _deposit() mode 2 calls convert with _minRec pinned to zero, so the swap has no slippage floor at all and the caller can sandwich their own transaction. Assuming the caller sets _mode to a value other than 1 or 2, can an unprivileged attacker turn this into a divergence between `mWomSV.getUserTotalLocked(account) read by getRewardAmount` and `the same read inside calDoubledCounted` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that every swap of protocol or user value must carry a slippage floor and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the internal convert leg passes a zero minimum received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() mode 2 calls convert with _minRec pinned to zero, so the swap has no slippage floor at all and the caller can sandwich their own transaction. Precondition: the caller sets _mode to a value other than 1 or 2.
- Invariant to test: every swap of protocol or user value must carry a slippage floor; concretely, `mWomSV.getUserTotalLocked(account) read by getRewardAmount` must stay reconciled with `the same read inside calDoubledCounted`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence atomically under the caller sets _mode to a value other than 1 or 2, asserting at the end that `mWomSV.getUserTotalLocked(account) read by getRewardAmount` still equals `the same read inside calDoubledCounted` and the PoC's balance delta is non-positive.

# Q2423: ArbWomUp3.incentiveDeposit - the internal convert leg passes a zero minimum received

## Question
Consider wombat/ArbWomUp3.sol, where _deposit() mode 2 calls convert with _minRec pinned to zero, so the swap has no slippage floor at all and the caller can sandwich their own transaction. Assuming the caller's mWomSV locked balance is zero so the correction is at its bottom bracket, can an unprivileged attacker turn this into a divergence between `IERC20(mWom).balanceOf(address(this))` and `the amount locked for _account in mode two` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that every swap of protocol or user value must carry a slippage floor and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the internal convert leg passes a zero minimum received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() mode 2 calls convert with _minRec pinned to zero, so the swap has no slippage floor at all and the caller can sandwich their own transaction. Precondition: the caller's mWomSV locked balance is zero so the correction is at its bottom bracket.
- Invariant to test: every swap of protocol or user value must carry a slippage floor; concretely, `IERC20(mWom).balanceOf(address(this))` must stay reconciled with `the amount locked for _account in mode two`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence atomically under the caller's mWomSV locked balance is zero so the correction is at its bottom bracket, asserting at the end that `IERC20(mWom).balanceOf(address(this))` still equals `the amount locked for _account in mode two` and the PoC's balance delta is non-positive.

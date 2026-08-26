# Q1580: ArbWomUp3.incentiveDeposit - the internal convert leg passes a zero minimum received

## Question
wombat/ArbWomUp3.sol: _deposit() mode 2 calls convert with _minRec pinned to zero, so the swap has no slippage floor at all and the caller can sandwich their own transaction. Under the caller sets _convertRatio to zero so the whole leg is swapped, is there an unprivileged sequence of `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` that leaves `rewardToSend` unreconciled with `IERC20(mgp).balanceOf(address(this))`, violates the invariant that every swap of protocol or user value must carry a slippage floor, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the internal convert leg passes a zero minimum received)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() mode 2 calls convert with _minRec pinned to zero, so the swap has no slippage floor at all and the caller can sandwich their own transaction. Precondition: the caller sets _convertRatio to zero so the whole leg is swapped.
- Invariant to test: every swap of protocol or user value must carry a slippage floor; concretely, `rewardToSend` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the caller sets _convertRatio to zero so the whole leg is swapped, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, then assert the victim's claimable value and the `rewardToSend` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.

# Q3479: SmartWomConvert.smartConvert - safeApprove without reset on the mWOM mint leg

## Question
wombat/SmartWomConvert.sol: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. With _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from under attacker control and the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, can an unprivileged caller sequence `smartConvert(uint256 _amountIn, uint256 _mode)` so that `_minRec` and `convertAmount + amountRec` no longer reconcile, violating the invariant that the mint leg must remain usable regardless of allowance residue and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: safeApprove without reset on the mWOM mint leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Precondition: the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two.
- Invariant to test: the mint leg must remain usable regardless of allowance residue; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, have the attacker run `smartConvert(uint256 _amountIn, uint256 _mode)`, then assert the victim's claimable value and the `_minRec` versus `convertAmount + amountRec` relation are unchanged by the attacker's transaction.

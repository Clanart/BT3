# Q3156: SmartWomConvert.convert - safeApprove without reset on the mWOM mint leg

## Question
wombat/SmartWomConvert.sol - _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Can an unprivileged attacker controlling _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR, under the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, exploit this through `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` to break the reconciliation between `maxSwapAmount()` and `IAsset(womAsset).cash() and IAsset(womAsset).liability()` and the invariant that the mint leg must remain usable regardless of allowance residue, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: safeApprove without reset on the mWOM mint leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Precondition: the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two.
- Invariant to test: the mint leg must remain usable regardless of allowance residue; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` sequence atomically under the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, asserting at the end that `maxSwapAmount()` still equals `IAsset(womAsset).cash() and IAsset(womAsset).liability()` and the PoC's balance delta is non-positive.

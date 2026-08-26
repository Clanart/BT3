# Q3445: SmartWomConvert.smartConvert - _minRec supplied by the same party that benefits from the swap

## Question
In wombat/SmartWomConvert.sol, _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Does `smartConvert(uint256 _amountIn, uint256 _mode)` let an unprivileged caller exploit that under the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, so that `maxSwapAmount()` diverges from `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, the invariant that the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: _minRec supplied by the same party that benefits from the swap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Precondition: the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two.
- Invariant to test: the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `smartConvert(uint256 _amountIn, uint256 _mode)` sequence atomically under the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, asserting at the end that `maxSwapAmount()` still equals `IAsset(womAsset).cash() and IAsset(womAsset).liability()` and the PoC's balance delta is non-positive.

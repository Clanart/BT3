# Q3754: SmartWomConvert.convertFor - _minRec supplied by the same party that benefits from the swap

## Question
wombat/SmartWomConvert.sol: _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. With _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound under attacker control and the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, can an unprivileged caller sequence `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` so that `maxSwapAmount()` and `IAsset(womAsset).cash() and IAsset(womAsset).liability()` no longer reconcile, violating the invariant that the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: _minRec supplied by the same party that benefits from the swap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound) under the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, asserting on every row that the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller.

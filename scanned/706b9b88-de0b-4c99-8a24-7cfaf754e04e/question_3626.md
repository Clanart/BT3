# Q3626: SmartWomConvert.convert - _minRec supplied by the same party that benefits from the swap

## Question
In wombat/SmartWomConvert.sol, _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Can an unprivileged attacker reach this through `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` while the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, and drive `currentRatio()` out of agreement with `buybackThreshold` - breaking the invariant that the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: _minRec supplied by the same party that benefits from the swap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` sequence atomically under the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, asserting at the end that `currentRatio()` still equals `buybackThreshold` and the PoC's balance delta is non-positive.

# Q3914: SmartWomConvert.smartConvert - _convertRatio is fully attacker-chosen

## Question
wombat/SmartWomConvert.sol: _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. With _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from under attacker control and the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, can an unprivileged caller sequence `smartConvert(uint256 _amountIn, uint256 _mode)` so that `maxSwapAmount()` and `IAsset(womAsset).cash() and IAsset(womAsset).liability()` no longer reconcile, violating the invariant that a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: _convertRatio is fully attacker-chosen)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `smartConvert(uint256 _amountIn, uint256 _mode)` sequence atomically under the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, asserting at the end that `maxSwapAmount()` still equals `IAsset(womAsset).cash() and IAsset(womAsset).liability()` and the PoC's balance delta is non-positive.

# Q4485: SmartWomConvert.convert - _convertRatio is fully attacker-chosen

## Question
Consider wombat/SmartWomConvert.sol, where _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Assuming the attacker sandwiches the transaction on the wom/mWom Wombat pool, can an unprivileged attacker turn this into a divergence between `maxSwapAmount()` and `IAsset(womAsset).cash() and IAsset(womAsset).liability()` via `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, breaking the invariant that a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: _convertRatio is fully attacker-chosen)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Precondition: the attacker sandwiches the transaction on the wom/mWom Wombat pool.
- Invariant to test: a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker sandwiches the transaction on the wom/mWom Wombat pool, then assert `maxSwapAmount()` and `IAsset(womAsset).cash() and IAsset(womAsset).liability()` end identical in both runs.

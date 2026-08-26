# Q5244: SmartWomConvert.smartConvert - smartConvert is on the protocol harvest path

## Question
wombat/SmartWomConvert.sol: WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. Under _convertRatio is set to zero so the entire input goes through the AMM, is there an unprivileged sequence of `smartConvert(uint256 _amountIn, uint256 _mode)` that leaves `maxSwapAmount()` unreconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, violates the invariant that protocol fee conversion must not be exposed to a price the harvest caller can set, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert is on the protocol harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. Precondition: _convertRatio is set to zero so the entire input goes through the AMM.
- Invariant to test: protocol fee conversion must not be exposed to a price the harvest caller can set; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `smartConvert(uint256 _amountIn, uint256 _mode)` sequence atomically under _convertRatio is set to zero so the entire input goes through the AMM, asserting at the end that `maxSwapAmount()` still equals `IAsset(womAsset).cash() and IAsset(womAsset).liability()` and the PoC's balance delta is non-positive.

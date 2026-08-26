# Q3989: SmartWomConvert.smartConvert - maxSwapAmount derives from instantaneous cash and liability

## Question
Consider wombat/SmartWomConvert.sol, where maxSwapAmount() returns (womLiability - womCash) * ratio / DENOMINATOR from live IAsset reads, so the ceiling on how much the protocol will swap is set by pool state an attacker can move immediately before calling. Assuming the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, can an unprivileged attacker turn this into a divergence between `maxSwapAmount()` and `IAsset(womAsset).cash() and IAsset(womAsset).liability()` via `smartConvert(uint256 _amountIn, uint256 _mode)`, breaking the invariant that a protocol-owned trade ceiling must not be computed from state the caller can set in the same block and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: maxSwapAmount derives from instantaneous cash and liability)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: maxSwapAmount() returns (womLiability - womCash) * ratio / DENOMINATOR from live IAsset reads, so the ceiling on how much the protocol will swap is set by pool state an attacker can move immediately before calling. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: a protocol-owned trade ceiling must not be computed from state the caller can set in the same block; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, call `smartConvert(uint256 _amountIn, uint256 _mode)`, and assert `maxSwapAmount()` equals `IAsset(womAsset).cash() and IAsset(womAsset).liability()` and that no account can withdraw more than it put in.

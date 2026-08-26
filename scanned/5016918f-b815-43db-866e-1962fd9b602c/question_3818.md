# Q3818: SmartWomConvert.smartConvert - buyback swap executes with a hardcoded zero minimum

## Question
In wombat/SmartWomConvert.sol, _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Starting from a state where the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, can an unprivileged EOA use `smartConvert(uint256 _amountIn, uint256 _mode)` to leave `maxSwapAmount()` inconsistent with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, violating the invariant that every swap of protocol or user value must carry a caller-independent slippage floor and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: buyback swap executes with a hardcoded zero minimum)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: every swap of protocol or user value must carry a caller-independent slippage floor; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, snapshot `maxSwapAmount()` and `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, run the attacker's `smartConvert(uint256 _amountIn, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

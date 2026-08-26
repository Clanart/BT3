# Q4446: SmartWomConvert.convert - buyback swap executes with a hardcoded zero minimum

## Question
In wombat/SmartWomConvert.sol, _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Does `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` let an unprivileged caller exploit that under the attacker sandwiches the transaction on the wom/mWom Wombat pool, so that `maxSwapAmount()` diverges from `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, the invariant that every swap of protocol or user value must carry a caller-independent slippage floor is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: buyback swap executes with a hardcoded zero minimum)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Precondition: the attacker sandwiches the transaction on the wom/mWom Wombat pool.
- Invariant to test: every swap of protocol or user value must carry a caller-independent slippage floor; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker sandwiches the transaction on the wom/mWom Wombat pool, snapshot `maxSwapAmount()` and `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, run the attacker's `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

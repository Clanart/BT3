# Q0940: SmartWomConvert.convert - buyback swap executes with a hardcoded zero minimum

## Question
In wombat/SmartWomConvert.sol, _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Starting from a state where the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, can an unprivileged EOA use `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` to leave `maxSwapAmount()` inconsistent with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, violating the invariant that every swap of protocol or user value must carry a caller-independent slippage floor and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: buyback swap executes with a hardcoded zero minimum)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Precondition: the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs.
- Invariant to test: every swap of protocol or user value must carry a caller-independent slippage floor; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`: constrain the setup so that the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, fuzz the attacker inputs (_convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR), and assert after every call that every swap of protocol or user value must carry a caller-independent slippage floor.

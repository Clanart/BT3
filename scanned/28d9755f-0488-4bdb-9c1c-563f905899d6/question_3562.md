# Q3562: SmartWomConvert.convert - buyback swap executes with a hardcoded zero minimum

## Question
wombat/SmartWomConvert.sol: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. With _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR under attacker control and the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, can an unprivileged caller sequence `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` so that `_convertRatio` and `DENOMINATOR` no longer reconcile, violating the invariant that every swap of protocol or user value must carry a caller-independent slippage floor and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: buyback swap executes with a hardcoded zero minimum)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: every swap of protocol or user value must carry a caller-independent slippage floor; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, then assert `_convertRatio` and `DENOMINATOR` end identical in both runs.

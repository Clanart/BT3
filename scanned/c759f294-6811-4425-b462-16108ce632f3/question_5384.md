# Q5384: SmartWomConvert.convertFor - buyback swap executes with a hardcoded zero minimum

## Question
In wombat/SmartWomConvert.sol, _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Can an unprivileged attacker reach this through `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` while _convertRatio is set to DENOMINATOR so nothing is swapped, and drive `_convertRatio` out of agreement with `DENOMINATOR` - breaking the invariant that every swap of protocol or user value must carry a caller-independent slippage floor - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: buyback swap executes with a hardcoded zero minimum)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Precondition: _convertRatio is set to DENOMINATOR so nothing is swapped.
- Invariant to test: every swap of protocol or user value must carry a caller-independent slippage floor; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under _convertRatio is set to DENOMINATOR so nothing is swapped, then assert `_convertRatio` and `DENOMINATOR` end identical in both runs.

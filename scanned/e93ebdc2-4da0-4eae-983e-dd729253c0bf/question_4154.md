# Q4154: SmartWomConvert.convertFor - buyback swap executes with a hardcoded zero minimum

## Question
Consider wombat/SmartWomConvert.sol, where _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Assuming a residual mWOM balance from an earlier rounding sits in the contract, can an unprivileged attacker turn this into a divergence between `maxSwapAmount()` and `IAsset(womAsset).cash() and IAsset(womAsset).liability()` via `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`, breaking the invariant that every swap of protocol or user value must carry a caller-independent slippage floor and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: buyback swap executes with a hardcoded zero minimum)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Precondition: a residual mWOM balance from an earlier rounding sits in the contract.
- Invariant to test: every swap of protocol or user value must carry a caller-independent slippage floor; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange a residual mWOM balance from an earlier rounding sits in the contract, call `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`, and assert `maxSwapAmount()` equals `IAsset(womAsset).cash() and IAsset(womAsset).liability()` and that no account can withdraw more than it put in.

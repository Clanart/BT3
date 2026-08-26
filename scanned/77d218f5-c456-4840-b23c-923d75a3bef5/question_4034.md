# Q4034: SmartWomConvert.convert - buyback swap executes with a hardcoded zero minimum

## Question
wombat/SmartWomConvert.sol: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. With _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR under attacker control and a residual mWOM balance from an earlier rounding sits in the contract, can an unprivileged caller sequence `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` so that `currentRatio()` and `buybackThreshold` no longer reconcile, violating the invariant that every swap of protocol or user value must carry a caller-independent slippage floor and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: buyback swap executes with a hardcoded zero minimum)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Precondition: a residual mWOM balance from an earlier rounding sits in the contract.
- Invariant to test: every swap of protocol or user value must carry a caller-independent slippage floor; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up a residual mWOM balance from an earlier rounding sits in the contract, snapshot `currentRatio()` and `buybackThreshold`, run the attacker's `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

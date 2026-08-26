# Q2456: SmartWomConvert.convert - buyback swap executes with a hardcoded zero minimum

## Question
wombat/SmartWomConvert.sol: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Under the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, is there an unprivileged sequence of `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` that leaves `_minRec` unreconciled with `convertAmount + amountRec`, violates the invariant that every swap of protocol or user value must carry a caller-independent slippage floor, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: buyback swap executes with a hardcoded zero minimum)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Precondition: the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn.
- Invariant to test: every swap of protocol or user value must carry a caller-independent slippage floor; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, call `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, and assert `_minRec` equals `convertAmount + amountRec` and that no account can withdraw more than it put in.

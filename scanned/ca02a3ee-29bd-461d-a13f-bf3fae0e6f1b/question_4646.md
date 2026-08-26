# Q4646: SmartWomConvert.smartConvert - buyback swap executes with a hardcoded zero minimum

## Question
wombat/SmartWomConvert.sol: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Under the attacker sandwiches the transaction on the wom/mWom Wombat pool, is there an unprivileged sequence of `smartConvert(uint256 _amountIn, uint256 _mode)` that leaves `_minRec` unreconciled with `convertAmount + amountRec`, violates the invariant that every swap of protocol or user value must carry a caller-independent slippage floor, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: buyback swap executes with a hardcoded zero minimum)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Precondition: the attacker sandwiches the transaction on the wom/mWom Wombat pool.
- Invariant to test: every swap of protocol or user value must carry a caller-independent slippage floor; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `smartConvert(uint256 _amountIn, uint256 _mode)` sequence atomically under the attacker sandwiches the transaction on the wom/mWom Wombat pool, asserting at the end that `_minRec` still equals `convertAmount + amountRec` and the PoC's balance delta is non-positive.

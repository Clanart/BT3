# Q2134: SmartWomConvert.smartConvert - buyback swap executes with a hardcoded zero minimum

## Question
wombat/SmartWomConvert.sol: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Under womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR, is there an unprivileged sequence of `smartConvert(uint256 _amountIn, uint256 _mode)` that leaves `obtainedmWomAmount` unreconciled with `IERC20(mWom).balanceOf(address(this))`, violates the invariant that every swap of protocol or user value must carry a caller-independent slippage floor, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: buyback swap executes with a hardcoded zero minimum)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Precondition: womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR.
- Invariant to test: every swap of protocol or user value must carry a caller-independent slippage floor; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR, snapshot `obtainedmWomAmount` and `IERC20(mWom).balanceOf(address(this))`, run the attacker's `smartConvert(uint256 _amountIn, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

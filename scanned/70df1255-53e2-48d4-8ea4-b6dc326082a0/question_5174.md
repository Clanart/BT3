# Q5174: SmartWomConvert.convertFor - buyback swap executes with a hardcoded zero minimum

## Question
Note that in wombat/SmartWomConvert.sol, _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Can an attacker holding only tokens bought on market reach it via `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` under _convertRatio is set to zero so the entire input goes through the AMM and force `obtainedmWomAmount` apart from `IERC20(mWom).balanceOf(address(this))`, breaking the invariant that every swap of protocol or user value must carry a caller-independent slippage floor for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: buyback swap executes with a hardcoded zero minimum)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Precondition: _convertRatio is set to zero so the entire input goes through the AMM.
- Invariant to test: every swap of protocol or user value must carry a caller-independent slippage floor; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`: constrain the setup so that _convertRatio is set to zero so the entire input goes through the AMM, fuzz the attacker inputs (_for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound), and assert after every call that every swap of protocol or user value must carry a caller-independent slippage floor.

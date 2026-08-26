# Q4814: SmartWomConvert.convert - buyback swap executes with a hardcoded zero minimum

## Question
In wombat/SmartWomConvert.sol, _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Does `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` let an unprivileged caller exploit that under the router leaves a non-zero allowance after the swap, so that `amountRec from swapExactTokensForTokens` diverges from `convertAmount minted 1:1 by IMWom(mWom).deposit`, the invariant that every swap of protocol or user value must carry a caller-independent slippage floor is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: buyback swap executes with a hardcoded zero minimum)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp) with the minimum output pinned to zero, so the swap leg itself has no slippage floor. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: every swap of protocol or user value must carry a caller-independent slippage floor; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the router leaves a non-zero allowance after the swap, have the attacker run `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, then assert the victim's claimable value and the `amountRec from swapExactTokensForTokens` versus `convertAmount minted 1:1 by IMWom(mWom).deposit` relation are unchanged by the attacker's transaction.

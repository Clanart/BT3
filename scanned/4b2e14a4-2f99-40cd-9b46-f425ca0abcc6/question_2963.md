# Q2963: SmartWomConvert.smartConvert - safeApprove without reset on the mWOM mint leg

## Question
In wombat/SmartWomConvert.sol, _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Does `smartConvert(uint256 _amountIn, uint256 _mode)` let an unprivileged caller exploit that under the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, so that `amountRec from swapExactTokensForTokens` diverges from `convertAmount minted 1:1 by IMWom(mWom).deposit`, the invariant that the mint leg must remain usable regardless of allowance residue is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: safeApprove without reset on the mWOM mint leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Precondition: the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn.
- Invariant to test: the mint leg must remain usable regardless of allowance residue; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from) under the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, asserting on every row that the mint leg must remain usable regardless of allowance residue.

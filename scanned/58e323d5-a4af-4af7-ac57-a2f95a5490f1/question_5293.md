# Q5293: SmartWomConvert.smartConvert - safeApprove without reset on the mWOM mint leg

## Question
wombat/SmartWomConvert.sol: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Under _convertRatio is set to zero so the entire input goes through the AMM, is there an unprivileged sequence of `smartConvert(uint256 _amountIn, uint256 _mode)` that leaves `amountRec from swapExactTokensForTokens` unreconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`, violates the invariant that the mint leg must remain usable regardless of allowance residue, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: safeApprove without reset on the mWOM mint leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Precondition: _convertRatio is set to zero so the entire input goes through the AMM.
- Invariant to test: the mint leg must remain usable regardless of allowance residue; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `smartConvert(uint256 _amountIn, uint256 _mode)` sequence atomically under _convertRatio is set to zero so the entire input goes through the AMM, asserting at the end that `amountRec from swapExactTokensForTokens` still equals `convertAmount minted 1:1 by IMWom(mWom).deposit` and the PoC's balance delta is non-positive.

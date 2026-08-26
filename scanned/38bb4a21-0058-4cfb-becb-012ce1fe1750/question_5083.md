# Q5083: SmartWomConvert.smartConvert - safeApprove without reset on the mWOM mint leg

## Question
In wombat/SmartWomConvert.sol, _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Does `smartConvert(uint256 _amountIn, uint256 _mode)` let an unprivileged caller exploit that under the router leaves a non-zero allowance after the swap, so that `maxSwapAmount()` diverges from `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, the invariant that the mint leg must remain usable regardless of allowance residue is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: safeApprove without reset on the mWOM mint leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: the mint leg must remain usable regardless of allowance residue; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the router leaves a non-zero allowance after the swap, then assert `maxSwapAmount()` and `IAsset(womAsset).cash() and IAsset(womAsset).liability()` end identical in both runs.

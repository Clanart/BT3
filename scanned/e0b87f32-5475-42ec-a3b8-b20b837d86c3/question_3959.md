# Q3959: SmartWomConvert.smartConvert - safeApprove without reset on the mWOM mint leg

## Question
wombat/SmartWomConvert.sol: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. With _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from under attacker control and the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, can an unprivileged caller sequence `smartConvert(uint256 _amountIn, uint256 _mode)` so that `obtainedmWomAmount` and `IERC20(mWom).balanceOf(address(this))` no longer reconcile, violating the invariant that the mint leg must remain usable regardless of allowance residue and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: safeApprove without reset on the mWOM mint leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: the mint leg must remain usable regardless of allowance residue; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, then assert `obtainedmWomAmount` and `IERC20(mWom).balanceOf(address(this))` end identical in both runs.

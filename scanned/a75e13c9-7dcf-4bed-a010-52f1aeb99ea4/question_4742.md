# Q4742: SmartWomConvert.smartConvert - safeApprove without reset on the router leg

## Question
wombat/SmartWomConvert.sol: _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Under the attacker sandwiches the transaction on the wom/mWom Wombat pool, is there an unprivileged sequence of `smartConvert(uint256 _amountIn, uint256 _mode)` that leaves `_convertRatio` unreconciled with `DENOMINATOR`, violates the invariant that an approval on a repeated path must be idempotent, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: safeApprove without reset on the router leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Precondition: the attacker sandwiches the transaction on the wom/mWom Wombat pool.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker sandwiches the transaction on the wom/mWom Wombat pool, then assert `_convertRatio` and `DENOMINATOR` end identical in both runs.

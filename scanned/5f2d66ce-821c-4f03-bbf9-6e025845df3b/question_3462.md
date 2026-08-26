# Q3462: SmartWomConvert.smartConvert - safeApprove without reset on the router leg

## Question
wombat/SmartWomConvert.sol: _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Under the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, is there an unprivileged sequence of `smartConvert(uint256 _amountIn, uint256 _mode)` that leaves `amountRec from swapExactTokensForTokens` unreconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`, violates the invariant that an approval on a repeated path must be idempotent, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: safeApprove without reset on the router leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Precondition: the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, call `smartConvert(uint256 _amountIn, uint256 _mode)`, and assert `amountRec from swapExactTokensForTokens` equals `convertAmount minted 1:1 by IMWom(mWom).deposit` and that no account can withdraw more than it put in.

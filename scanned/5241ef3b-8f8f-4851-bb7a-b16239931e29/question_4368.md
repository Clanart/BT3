# Q4368: SmartWomConvert.smartConvert - safeApprove without reset on the router leg

## Question
wombat/SmartWomConvert.sol: _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. With _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from under attacker control and a residual mWOM balance from an earlier rounding sits in the contract, can an unprivileged caller sequence `smartConvert(uint256 _amountIn, uint256 _mode)` so that `obtainedmWomAmount` and `IERC20(mWom).balanceOf(address(this))` no longer reconcile, violating the invariant that an approval on a repeated path must be idempotent and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: safeApprove without reset on the router leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Precondition: a residual mWOM balance from an earlier rounding sits in the contract.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from) under a residual mWOM balance from an earlier rounding sits in the contract, asserting on every row that an approval on a repeated path must be idempotent.

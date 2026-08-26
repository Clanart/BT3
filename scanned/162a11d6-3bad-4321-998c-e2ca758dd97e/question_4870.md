# Q4870: SmartWomConvert.convert - safeApprove without reset on the router leg

## Question
In wombat/SmartWomConvert.sol, _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Does `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` let an unprivileged caller exploit that under the router leaves a non-zero allowance after the swap, so that `obtainedmWomAmount` diverges from `IERC20(mWom).balanceOf(address(this))`, the invariant that an approval on a repeated path must be idempotent is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: safeApprove without reset on the router leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR) under the router leaves a non-zero allowance after the swap, asserting on every row that an approval on a repeated path must be idempotent.

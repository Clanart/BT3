# Q1617: SmartWomConvert.smartConvert - safeApprove without reset on the router leg

## Question
Consider wombat/SmartWomConvert.sol, where _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Assuming the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, can an unprivileged attacker turn this into a divergence between `_convertRatio` and `DENOMINATOR` via `smartConvert(uint256 _amountIn, uint256 _mode)`, breaking the invariant that an approval on a repeated path must be idempotent and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: safeApprove without reset on the router leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Precondition: the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, call `smartConvert(uint256 _amountIn, uint256 _mode)`, and assert `_convertRatio` equals `DENOMINATOR` and that no account can withdraw more than it put in.

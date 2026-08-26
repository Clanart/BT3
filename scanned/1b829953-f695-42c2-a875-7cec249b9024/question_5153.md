# Q5153: SmartWomConvert.convert - safeApprove without reset on the router leg

## Question
In wombat/SmartWomConvert.sol, _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Can an unprivileged attacker reach this through `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` while _convertRatio is set to zero so the entire input goes through the AMM, and drive `_convertRatio` out of agreement with `DENOMINATOR` - breaking the invariant that an approval on a repeated path must be idempotent - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: safeApprove without reset on the router leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Precondition: _convertRatio is set to zero so the entire input goes through the AMM.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under _convertRatio is set to zero so the entire input goes through the AMM, then assert `_convertRatio` and `DENOMINATOR` end identical in both runs.

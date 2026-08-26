# Q5209: SmartWomConvert.convertFor - safeApprove without reset on the router leg

## Question
wombat/SmartWomConvert.sol: _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. With _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound under attacker control and _convertRatio is set to zero so the entire input goes through the AMM, can an unprivileged caller sequence `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` so that `currentRatio()` and `buybackThreshold` no longer reconcile, violating the invariant that an approval on a repeated path must be idempotent and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: safeApprove without reset on the router leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Precondition: _convertRatio is set to zero so the entire input goes through the AMM.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under _convertRatio is set to zero so the entire input goes through the AMM, then assert `currentRatio()` and `buybackThreshold` end identical in both runs.

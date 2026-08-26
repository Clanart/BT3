# Q4537: SmartWomConvert.convert - mode selects a materially different settlement with no validation

## Question
Consider wombat/SmartWomConvert.sol, where _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Assuming the attacker sandwiches the transaction on the wom/mWom Wombat pool, can an unprivileged attacker turn this into a divergence between `_convertRatio` and `DENOMINATOR` via `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, breaking the invariant that an unrecognised routing mode must revert rather than silently take the least restrictive path and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: mode selects a materially different settlement with no validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Precondition: the attacker sandwiches the transaction on the wom/mWom Wombat pool.
- Invariant to test: an unrecognised routing mode must revert rather than silently take the least restrictive path; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker sandwiches the transaction on the wom/mWom Wombat pool, then assert `_convertRatio` and `DENOMINATOR` end identical in both runs.

# Q3674: SmartWomConvert.convert - mode selects a materially different settlement with no validation

## Question
In wombat/SmartWomConvert.sol, _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Starting from a state where the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, can an unprivileged EOA use `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` to leave `_minRec` inconsistent with `convertAmount + amountRec`, violating the invariant that an unrecognised routing mode must revert rather than silently take the least restrictive path and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: mode selects a materially different settlement with no validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: an unrecognised routing mode must revert rather than silently take the least restrictive path; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR) under the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, asserting on every row that an unrecognised routing mode must revert rather than silently take the least restrictive path.

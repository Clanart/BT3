# Q5433: SmartWomConvert.convertFor - mode selects a materially different settlement with no validation

## Question
In wombat/SmartWomConvert.sol, _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Starting from a state where _convertRatio is set to DENOMINATOR so nothing is swapped, can an unprivileged EOA use `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` to leave `_minRec` inconsistent with `convertAmount + amountRec`, violating the invariant that an unrecognised routing mode must revert rather than silently take the least restrictive path and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: mode selects a materially different settlement with no validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Precondition: _convertRatio is set to DENOMINATOR so nothing is swapped.
- Invariant to test: an unrecognised routing mode must revert rather than silently take the least restrictive path; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound) under _convertRatio is set to DENOMINATOR so nothing is swapped, asserting on every row that an unrecognised routing mode must revert rather than silently take the least restrictive path.

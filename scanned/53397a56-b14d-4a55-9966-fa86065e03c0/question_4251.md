# Q4251: SmartWomConvert.convertFor - mode selects a materially different settlement with no validation

## Question
In wombat/SmartWomConvert.sol, _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Does `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` let an unprivileged caller exploit that under a residual mWOM balance from an earlier rounding sits in the contract, so that `_convertRatio` diverges from `DENOMINATOR`, the invariant that an unrecognised routing mode must revert rather than silently take the least restrictive path is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: mode selects a materially different settlement with no validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Precondition: a residual mWOM balance from an earlier rounding sits in the contract.
- Invariant to test: an unrecognised routing mode must revert rather than silently take the least restrictive path; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a residual mWOM balance from an earlier rounding sits in the contract, then assert `_convertRatio` and `DENOMINATOR` end identical in both runs.

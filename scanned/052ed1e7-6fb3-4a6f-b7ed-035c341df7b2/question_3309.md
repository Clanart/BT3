# Q3309: SmartWomConvert.convertFor - mode selects a materially different settlement with no validation

## Question
wombat/SmartWomConvert.sol: _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Under the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, is there an unprivileged sequence of `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` that leaves `_minRec` unreconciled with `convertAmount + amountRec`, violates the invariant that an unrecognised routing mode must revert rather than silently take the least restrictive path, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: mode selects a materially different settlement with no validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Precondition: the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two.
- Invariant to test: an unrecognised routing mode must revert rather than silently take the least restrictive path; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` sequence atomically under the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, asserting at the end that `_minRec` still equals `convertAmount + amountRec` and the PoC's balance delta is non-positive.

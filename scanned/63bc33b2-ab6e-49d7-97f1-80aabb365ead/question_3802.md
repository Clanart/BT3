# Q3802: SmartWomConvert.convertFor - mode selects a materially different settlement with no validation

## Question
wombat/SmartWomConvert.sol - _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Can an unprivileged attacker controlling _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound, under the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, exploit this through `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` to break the reconciliation between `obtainedmWomAmount` and `IERC20(mWom).balanceOf(address(this))` and the invariant that an unrecognised routing mode must revert rather than silently take the least restrictive path, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: mode selects a materially different settlement with no validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: an unrecognised routing mode must revert rather than silently take the least restrictive path; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, call `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`, and assert `obtainedmWomAmount` equals `IERC20(mWom).balanceOf(address(this))` and that no account can withdraw more than it put in.

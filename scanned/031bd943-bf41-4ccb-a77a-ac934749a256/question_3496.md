# Q3496: SmartWomConvert.smartConvert - mode selects a materially different settlement with no validation

## Question
In wombat/SmartWomConvert.sol, _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Starting from a state where the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, can an unprivileged EOA use `smartConvert(uint256 _amountIn, uint256 _mode)` to leave `obtainedmWomAmount` inconsistent with `IERC20(mWom).balanceOf(address(this))`, violating the invariant that an unrecognised routing mode must revert rather than silently take the least restrictive path and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: mode selects a materially different settlement with no validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Precondition: the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two.
- Invariant to test: an unrecognised routing mode must revert rather than silently take the least restrictive path; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `smartConvert(uint256 _amountIn, uint256 _mode)`: constrain the setup so that the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, fuzz the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from), and assert after every call that an unrecognised routing mode must revert rather than silently take the least restrictive path.

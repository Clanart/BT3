# Q4394: SmartWomConvert.smartConvert - mode selects a materially different settlement with no validation

## Question
In wombat/SmartWomConvert.sol, _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Does `smartConvert(uint256 _amountIn, uint256 _mode)` let an unprivileged caller exploit that under a residual mWOM balance from an earlier rounding sits in the contract, so that `currentRatio()` diverges from `buybackThreshold`, the invariant that an unrecognised routing mode must revert rather than silently take the least restrictive path is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: mode selects a materially different settlement with no validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Precondition: a residual mWOM balance from an earlier rounding sits in the contract.
- Invariant to test: an unrecognised routing mode must revert rather than silently take the least restrictive path; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from) under a residual mWOM balance from an earlier rounding sits in the contract, asserting on every row that an unrecognised routing mode must revert rather than silently take the least restrictive path.

# Q4892: SmartWomConvert.convert - mode selects a materially different settlement with no validation

## Question
In wombat/SmartWomConvert.sol, _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Does `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` let an unprivileged caller exploit that under the router leaves a non-zero allowance after the swap, so that `currentRatio()` diverges from `buybackThreshold`, the invariant that an unrecognised routing mode must revert rather than silently take the least restrictive path is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: mode selects a materially different settlement with no validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: an unrecognised routing mode must revert rather than silently take the least restrictive path; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`: constrain the setup so that the router leaves a non-zero allowance after the swap, fuzz the attacker inputs (_convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR), and assert after every call that an unrecognised routing mode must revert rather than silently take the least restrictive path.

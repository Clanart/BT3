# Q4980: SmartWomConvert.convertFor - mode selects a materially different settlement with no validation

## Question
Note that in wombat/SmartWomConvert.sol, _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Can an attacker holding only tokens bought on market reach it via `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` under the router leaves a non-zero allowance after the swap and force `maxSwapAmount()` apart from `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, breaking the invariant that an unrecognised routing mode must revert rather than silently take the least restrictive path for High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: mode selects a materially different settlement with no validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: an unrecognised routing mode must revert rather than silently take the least restrictive path; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound) under the router leaves a non-zero allowance after the swap, asserting on every row that an unrecognised routing mode must revert rather than silently take the least restrictive path.

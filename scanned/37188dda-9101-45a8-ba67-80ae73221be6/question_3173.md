# Q3173: SmartWomConvert.convert - mode selects a materially different settlement with no validation

## Question
Note that in wombat/SmartWomConvert.sol, _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Can an attacker holding only tokens bought on market reach it via `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` under the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two and force `amountRec from swapExactTokensForTokens` apart from `convertAmount minted 1:1 by IMWom(mWom).deposit`, breaking the invariant that an unrecognised routing mode must revert rather than silently take the least restrictive path for High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: mode selects a materially different settlement with no validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Precondition: the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two.
- Invariant to test: an unrecognised routing mode must revert rather than silently take the least restrictive path; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, call `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, and assert `amountRec from swapExactTokensForTokens` equals `convertAmount minted 1:1 by IMWom(mWom).deposit` and that no account can withdraw more than it put in.

# Q2364: SmartWomConvert.smartConvert - mode selects a materially different settlement with no validation

## Question
wombat/SmartWomConvert.sol: _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Under womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR, is there an unprivileged sequence of `smartConvert(uint256 _amountIn, uint256 _mode)` that leaves `amountRec from swapExactTokensForTokens` unreconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`, violates the invariant that an unrecognised routing mode must revert rather than silently take the least restrictive path, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: mode selects a materially different settlement with no validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() treats _mode == 1 as a MasterMagpie deposit for _for, _mode == 2 as an mWomSV lockFor, and anything else as a plain transfer, so an unrecognised mode silently falls through to the transfer branch. Precondition: womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR.
- Invariant to test: an unrecognised routing mode must revert rather than silently take the least restrictive path; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `smartConvert(uint256 _amountIn, uint256 _mode)`: constrain the setup so that womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR, fuzz the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from), and assert after every call that an unrecognised routing mode must revert rather than silently take the least restrictive path.

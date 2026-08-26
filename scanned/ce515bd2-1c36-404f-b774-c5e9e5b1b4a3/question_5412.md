# Q5412: SmartWomConvert.convertFor - _minRec supplied by the same party that benefits from the swap

## Question
In wombat/SmartWomConvert.sol, _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Starting from a state where _convertRatio is set to DENOMINATOR so nothing is swapped, can an unprivileged EOA use `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` to leave `currentRatio()` inconsistent with `buybackThreshold`, violating the invariant that the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: _minRec supplied by the same party that benefits from the swap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Precondition: _convertRatio is set to DENOMINATOR so nothing is swapped.
- Invariant to test: the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under _convertRatio is set to DENOMINATOR so nothing is swapped, then assert `currentRatio()` and `buybackThreshold` end identical in both runs.

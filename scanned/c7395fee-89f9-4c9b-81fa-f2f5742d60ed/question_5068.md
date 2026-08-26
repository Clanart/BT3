# Q5068: SmartWomConvert.smartConvert - _minRec supplied by the same party that benefits from the swap

## Question
wombat/SmartWomConvert.sol - _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Can an unprivileged attacker controlling _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from, under the router leaves a non-zero allowance after the swap, exploit this through `smartConvert(uint256 _amountIn, uint256 _mode)` to break the reconciliation between `_convertRatio` and `DENOMINATOR` and the invariant that the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: _minRec supplied by the same party that benefits from the swap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the router leaves a non-zero allowance after the swap, snapshot `_convertRatio` and `DENOMINATOR`, run the attacker's `smartConvert(uint256 _amountIn, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

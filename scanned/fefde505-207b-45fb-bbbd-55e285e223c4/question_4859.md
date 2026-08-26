# Q4859: SmartWomConvert.convert - _minRec supplied by the same party that benefits from the swap

## Question
wombat/SmartWomConvert.sol: _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. With _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR under attacker control and the router leaves a non-zero allowance after the swap, can an unprivileged caller sequence `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` so that `_minRec` and `convertAmount + amountRec` no longer reconcile, violating the invariant that the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: _minRec supplied by the same party that benefits from the swap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the router leaves a non-zero allowance after the swap, call `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, and assert `_minRec` equals `convertAmount + amountRec` and that no account can withdraw more than it put in.

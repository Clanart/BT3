# Q3929: SmartWomConvert.smartConvert - _minRec supplied by the same party that benefits from the swap

## Question
wombat/SmartWomConvert.sol: _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. With _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from under attacker control and the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, can an unprivileged caller sequence `smartConvert(uint256 _amountIn, uint256 _mode)` so that `amountRec from swapExactTokensForTokens` and `convertAmount minted 1:1 by IMWom(mWom).deposit` no longer reconcile, violating the invariant that the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: _minRec supplied by the same party that benefits from the swap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from) under the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, asserting on every row that the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller.

# Q4355: SmartWomConvert.smartConvert - _minRec supplied by the same party that benefits from the swap

## Question
In wombat/SmartWomConvert.sol, _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Starting from a state where a residual mWOM balance from an earlier rounding sits in the contract, can an unprivileged EOA use `smartConvert(uint256 _amountIn, uint256 _mode)` to leave `_minRec` inconsistent with `convertAmount + amountRec`, violating the invariant that the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: _minRec supplied by the same party that benefits from the swap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Precondition: a residual mWOM balance from an earlier rounding sits in the contract.
- Invariant to test: the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from) under a residual mWOM balance from an earlier rounding sits in the contract, asserting on every row that the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller.

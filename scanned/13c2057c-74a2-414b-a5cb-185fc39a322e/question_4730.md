# Q4730: SmartWomConvert.smartConvert - _minRec supplied by the same party that benefits from the swap

## Question
Consider wombat/SmartWomConvert.sol, where _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Assuming the attacker sandwiches the transaction on the wom/mWom Wombat pool, can an unprivileged attacker turn this into a divergence between `obtainedmWomAmount` and `IERC20(mWom).balanceOf(address(this))` via `smartConvert(uint256 _amountIn, uint256 _mode)`, breaking the invariant that the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: _minRec supplied by the same party that benefits from the swap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Precondition: the attacker sandwiches the transaction on the wom/mWom Wombat pool.
- Invariant to test: the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `smartConvert(uint256 _amountIn, uint256 _mode)` sequence atomically under the attacker sandwiches the transaction on the wom/mWom Wombat pool, asserting at the end that `obtainedmWomAmount` still equals `IERC20(mWom).balanceOf(address(this))` and the PoC's balance delta is non-positive.

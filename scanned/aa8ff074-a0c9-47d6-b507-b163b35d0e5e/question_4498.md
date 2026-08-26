# Q4498: SmartWomConvert.convert - _minRec supplied by the same party that benefits from the swap

## Question
In wombat/SmartWomConvert.sol, _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Can an unprivileged attacker reach this through `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` while the attacker sandwiches the transaction on the wom/mWom Wombat pool, and drive `amountRec from swapExactTokensForTokens` out of agreement with `convertAmount minted 1:1 by IMWom(mWom).deposit` - breaking the invariant that the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: _minRec supplied by the same party that benefits from the swap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Precondition: the attacker sandwiches the transaction on the wom/mWom Wombat pool.
- Invariant to test: the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker sandwiches the transaction on the wom/mWom Wombat pool, then assert `amountRec from swapExactTokensForTokens` and `convertAmount minted 1:1 by IMWom(mWom).deposit` end identical in both runs.

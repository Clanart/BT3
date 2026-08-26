# Q2925: SmartWomConvert.smartConvert - _minRec supplied by the same party that benefits from the swap

## Question
In wombat/SmartWomConvert.sol, _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Does `smartConvert(uint256 _amountIn, uint256 _mode)` let an unprivileged caller exploit that under the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, so that `currentRatio()` diverges from `buybackThreshold`, the invariant that the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: _minRec supplied by the same party that benefits from the swap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() checks convertAmount + amountRec against the caller-supplied _minRec, so on the ManualCompound path the compounding caller sets the slippage floor for value that is not entirely theirs. Precondition: the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn.
- Invariant to test: the slippage floor for a shared-balance swap must be derived from protocol state, not from the caller; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, call `smartConvert(uint256 _amountIn, uint256 _mode)`, and assert `currentRatio()` equals `buybackThreshold` and that no account can withdraw more than it put in.

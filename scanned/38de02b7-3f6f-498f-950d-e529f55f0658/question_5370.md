# Q5370: SmartWomConvert.convert - safeApprove without reset on the mWOM mint leg

## Question
Consider wombat/SmartWomConvert.sol, where _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Assuming _convertRatio is set to DENOMINATOR so nothing is swapped, can an unprivileged attacker turn this into a divergence between `maxSwapAmount()` and `IAsset(womAsset).cash() and IAsset(womAsset).liability()` via `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, breaking the invariant that the mint leg must remain usable regardless of allowance residue and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: safeApprove without reset on the mWOM mint leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Precondition: _convertRatio is set to DENOMINATOR so nothing is swapped.
- Invariant to test: the mint leg must remain usable regardless of allowance residue; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish _convertRatio is set to DENOMINATOR so nothing is swapped, have the attacker run `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, then assert the victim's claimable value and the `maxSwapAmount()` versus `IAsset(womAsset).cash() and IAsset(womAsset).liability()` relation are unchanged by the attacker's transaction.

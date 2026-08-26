# Q2571: SmartWomConvert.convert - safeApprove without reset on the router leg

## Question
Consider wombat/SmartWomConvert.sol, where _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Assuming the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, can an unprivileged attacker turn this into a divergence between `_convertRatio` and `DENOMINATOR` via `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, breaking the invariant that an approval on a repeated path must be idempotent and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: safeApprove without reset on the router leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Precondition: the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, have the attacker run `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, then assert the victim's claimable value and the `_convertRatio` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.

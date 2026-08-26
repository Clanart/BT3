# Q2525: SmartWomConvert.convert - _convertRatio is fully attacker-chosen

## Question
Consider wombat/SmartWomConvert.sol, where _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Assuming the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, can an unprivileged attacker turn this into a divergence between `_minRec` and `convertAmount + amountRec` via `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, breaking the invariant that a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: _convertRatio is fully attacker-chosen)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Precondition: the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn.
- Invariant to test: a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR) under the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, asserting on every row that a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path.

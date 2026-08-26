# Q3610: SmartWomConvert.convert - _convertRatio is fully attacker-chosen

## Question
wombat/SmartWomConvert.sol: _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Under the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, is there an unprivileged sequence of `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` that leaves `_convertRatio` unreconciled with `DENOMINATOR`, violates the invariant that a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: _convertRatio is fully attacker-chosen)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, call `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, and assert `_convertRatio` equals `DENOMINATOR` and that no account can withdraw more than it put in.

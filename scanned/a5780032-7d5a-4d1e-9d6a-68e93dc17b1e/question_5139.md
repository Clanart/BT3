# Q5139: SmartWomConvert.convert - _convertRatio is fully attacker-chosen

## Question
In wombat/SmartWomConvert.sol, _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Can an unprivileged attacker reach this through `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` while _convertRatio is set to zero so the entire input goes through the AMM, and drive `_minRec` out of agreement with `convertAmount + amountRec` - breaking the invariant that a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: _convertRatio is fully attacker-chosen)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Precondition: _convertRatio is set to zero so the entire input goes through the AMM.
- Invariant to test: a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange _convertRatio is set to zero so the entire input goes through the AMM, call `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, and assert `_minRec` equals `convertAmount + amountRec` and that no account can withdraw more than it put in.

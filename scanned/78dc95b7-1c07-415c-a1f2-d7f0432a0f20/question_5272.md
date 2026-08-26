# Q5272: SmartWomConvert.smartConvert - _convertRatio is fully attacker-chosen

## Question
wombat/SmartWomConvert.sol: _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Under _convertRatio is set to zero so the entire input goes through the AMM, is there an unprivileged sequence of `smartConvert(uint256 _amountIn, uint256 _mode)` that leaves `_convertRatio` unreconciled with `DENOMINATOR`, violates the invariant that a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: _convertRatio is fully attacker-chosen)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() only rejects _convertRatio > DENOMINATOR, so a caller can force the entire input through the AMM leg, which matters because ManualCompound.compound forwards a caller-supplied _convertRatio while spending value that arrived from other users' claims. Precondition: _convertRatio is set to zero so the entire input goes through the AMM.
- Invariant to test: a routing parameter that decides how much protocol value is traded must not be caller-supplied on a shared-balance path; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange _convertRatio is set to zero so the entire input goes through the AMM, call `smartConvert(uint256 _amountIn, uint256 _mode)`, and assert `_convertRatio` equals `DENOMINATOR` and that no account can withdraw more than it put in.

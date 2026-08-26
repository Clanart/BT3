# Q5258: SmartWomConvert.smartConvert - obtained amount computed from arithmetic rather than from balance delta

## Question
wombat/SmartWomConvert.sol: _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Under _convertRatio is set to zero so the entire input goes through the AMM, is there an unprivileged sequence of `smartConvert(uint256 _amountIn, uint256 _mode)` that leaves `_minRec` unreconciled with `convertAmount + amountRec`, violates the invariant that the amount credited to a user must equal the balance the contract actually received for them, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: obtained amount computed from arithmetic rather than from balance delta)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Precondition: _convertRatio is set to zero so the entire input goes through the AMM.
- Invariant to test: the amount credited to a user must equal the balance the contract actually received for them; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from) under _convertRatio is set to zero so the entire input goes through the AMM, asserting on every row that the amount credited to a user must equal the balance the contract actually received for them.

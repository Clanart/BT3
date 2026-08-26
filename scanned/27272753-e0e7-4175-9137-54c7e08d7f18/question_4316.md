# Q4316: SmartWomConvert.smartConvert - obtained amount computed from arithmetic rather than from balance delta

## Question
wombat/SmartWomConvert.sol: _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. With _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from under attacker control and a residual mWOM balance from an earlier rounding sits in the contract, can an unprivileged caller sequence `smartConvert(uint256 _amountIn, uint256 _mode)` so that `currentRatio()` and `buybackThreshold` no longer reconcile, violating the invariant that the amount credited to a user must equal the balance the contract actually received for them and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: obtained amount computed from arithmetic rather than from balance delta)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Precondition: a residual mWOM balance from an earlier rounding sits in the contract.
- Invariant to test: the amount credited to a user must equal the balance the contract actually received for them; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from) under a residual mWOM balance from an earlier rounding sits in the contract, asserting on every row that the amount credited to a user must equal the balance the contract actually received for them.

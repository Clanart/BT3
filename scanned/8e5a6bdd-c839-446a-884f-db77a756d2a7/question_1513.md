# Q1513: SmartWomConvert.smartConvert - obtained amount computed from arithmetic rather than from balance delta

## Question
Note that in wombat/SmartWomConvert.sol, _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Can an attacker holding only tokens bought on market reach it via `smartConvert(uint256 _amountIn, uint256 _mode)` under the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs and force `maxSwapAmount()` apart from `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, breaking the invariant that the amount credited to a user must equal the balance the contract actually received for them for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: obtained amount computed from arithmetic rather than from balance delta)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Precondition: the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs.
- Invariant to test: the amount credited to a user must equal the balance the contract actually received for them; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, have the attacker run `smartConvert(uint256 _amountIn, uint256 _mode)`, then assert the victim's claimable value and the `maxSwapAmount()` versus `IAsset(womAsset).cash() and IAsset(womAsset).liability()` relation are unchanged by the attacker's transaction.

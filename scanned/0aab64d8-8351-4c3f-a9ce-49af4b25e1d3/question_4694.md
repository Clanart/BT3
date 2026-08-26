# Q4694: SmartWomConvert.smartConvert - obtained amount computed from arithmetic rather than from balance delta

## Question
In wombat/SmartWomConvert.sol, _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Starting from a state where the attacker sandwiches the transaction on the wom/mWom Wombat pool, can an unprivileged EOA use `smartConvert(uint256 _amountIn, uint256 _mode)` to leave `maxSwapAmount()` inconsistent with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, violating the invariant that the amount credited to a user must equal the balance the contract actually received for them and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: obtained amount computed from arithmetic rather than from balance delta)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Precondition: the attacker sandwiches the transaction on the wom/mWom Wombat pool.
- Invariant to test: the amount credited to a user must equal the balance the contract actually received for them; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker sandwiches the transaction on the wom/mWom Wombat pool, snapshot `maxSwapAmount()` and `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, run the attacker's `smartConvert(uint256 _amountIn, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

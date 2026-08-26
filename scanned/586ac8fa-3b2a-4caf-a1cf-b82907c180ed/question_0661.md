# Q0661: SmartWomConvert.smartConvert - shared mWOM balance is settled to whoever calls next

## Question
In wombat/SmartWomConvert.sol, mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Starting from a state where the attacker has pushed mWom below buybackThreshold against wom in the same transaction, can an unprivileged EOA use `smartConvert(uint256 _amountIn, uint256 _mode)` to leave `maxSwapAmount()` inconsistent with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, violating the invariant that one caller must never be settled out of value another caller left behind and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: the attacker has pushed mWom below buybackThreshold against wom in the same transaction.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker has pushed mWom below buybackThreshold against wom in the same transaction, snapshot `maxSwapAmount()` and `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, run the attacker's `smartConvert(uint256 _amountIn, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

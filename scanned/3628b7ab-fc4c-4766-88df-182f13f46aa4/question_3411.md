# Q3411: SmartWomConvert.smartConvert - shared mWOM balance is settled to whoever calls next

## Question
wombat/SmartWomConvert.sol: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. With _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from under attacker control and the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, can an unprivileged caller sequence `smartConvert(uint256 _amountIn, uint256 _mode)` so that `_convertRatio` and `DENOMINATOR` no longer reconcile, violating the invariant that one caller must never be settled out of value another caller left behind and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, then assert `_convertRatio` and `DENOMINATOR` end identical in both runs.

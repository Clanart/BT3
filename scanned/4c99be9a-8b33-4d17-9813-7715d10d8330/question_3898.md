# Q3898: SmartWomConvert.smartConvert - shared mWOM balance is settled to whoever calls next

## Question
In wombat/SmartWomConvert.sol, mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Can an unprivileged attacker reach this through `smartConvert(uint256 _amountIn, uint256 _mode)` while the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, and drive `currentRatio()` out of agreement with `buybackThreshold` - breaking the invariant that one caller must never be settled out of value another caller left behind - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, call `smartConvert(uint256 _amountIn, uint256 _mode)`, and assert `currentRatio()` equals `buybackThreshold` and that no account can withdraw more than it put in.

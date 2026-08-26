# Q3088: SmartWomConvert.convert - shared mWOM balance is settled to whoever calls next

## Question
wombat/SmartWomConvert.sol - mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Can an unprivileged attacker controlling _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR, under the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, exploit this through `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` to break the reconciliation between `_minRec` and `convertAmount + amountRec` and the invariant that one caller must never be settled out of value another caller left behind, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`: constrain the setup so that the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, fuzz the attacker inputs (_convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR), and assert after every call that one caller must never be settled out of value another caller left behind.

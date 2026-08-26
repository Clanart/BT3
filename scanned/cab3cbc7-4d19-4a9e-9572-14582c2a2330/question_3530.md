# Q3530: SmartWomConvert.depositFor - shared mWOM balance is settled to whoever calls next

## Question
wombat/SmartWomConvert.sol: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. With _amount and _for, with the mWOM pulled from the caller under attacker control and the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, can an unprivileged caller sequence `depositFor(uint256 _amount, address _for)` so that `currentRatio()` and `buybackThreshold` no longer reconcile, violating the invariant that one caller must never be settled out of value another caller left behind and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, with the mWOM pulled from the caller
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, then assert `currentRatio()` and `buybackThreshold` end identical in both runs.

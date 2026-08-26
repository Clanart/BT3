# Q4004: SmartWomConvert.depositFor - shared mWOM balance is settled to whoever calls next

## Question
In wombat/SmartWomConvert.sol, mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Starting from a state where the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, can an unprivileged EOA use `depositFor(uint256 _amount, address _for)` to leave `maxSwapAmount()` inconsistent with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, violating the invariant that one caller must never be settled out of value another caller left behind and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, with the mWOM pulled from the caller
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `depositFor(uint256 _amount, address _for)`: constrain the setup so that the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, fuzz the attacker inputs (_amount and _for, with the mWOM pulled from the caller), and assert after every call that one caller must never be settled out of value another caller left behind.

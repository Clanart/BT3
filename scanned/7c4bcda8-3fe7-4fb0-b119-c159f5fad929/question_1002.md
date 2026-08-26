# Q1002: SmartWomConvert.convert - shared mWOM balance is settled to whoever calls next

## Question
Note that in wombat/SmartWomConvert.sol, mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Can an attacker holding only tokens bought on market reach it via `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` under the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs and force `currentRatio()` apart from `buybackThreshold`, breaking the invariant that one caller must never be settled out of value another caller left behind for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR) under the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, asserting on every row that one caller must never be settled out of value another caller left behind.

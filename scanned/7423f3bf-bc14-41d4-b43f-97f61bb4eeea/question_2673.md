# Q2673: SmartWomConvert.convertFor - shared mWOM balance is settled to whoever calls next

## Question
Note that in wombat/SmartWomConvert.sol, mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Can an attacker holding only tokens bought on market reach it via `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` under the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn and force `_minRec` apart from `convertAmount + amountRec`, breaking the invariant that one caller must never be settled out of value another caller left behind for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound) under the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, asserting on every row that one caller must never be settled out of value another caller left behind.

# Q3594: SmartWomConvert.convert - shared mWOM balance is settled to whoever calls next

## Question
Note that in wombat/SmartWomConvert.sol, mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Can an attacker holding only tokens bought on market reach it via `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` under the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero and force `obtainedmWomAmount` apart from `IERC20(mWom).balanceOf(address(this))`, breaking the invariant that one caller must never be settled out of value another caller left behind for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, have the attacker run `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, then assert the victim's claimable value and the `obtainedmWomAmount` versus `IERC20(mWom).balanceOf(address(this))` relation are unchanged by the attacker's transaction.

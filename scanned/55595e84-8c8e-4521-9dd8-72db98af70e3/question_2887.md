# Q2887: SmartWomConvert.smartConvert - shared mWOM balance is settled to whoever calls next

## Question
In wombat/SmartWomConvert.sol, mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Does `smartConvert(uint256 _amountIn, uint256 _mode)` let an unprivileged caller exploit that under the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, so that `obtainedmWomAmount` diverges from `IERC20(mWom).balanceOf(address(this))`, the invariant that one caller must never be settled out of value another caller left behind is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, then assert `obtainedmWomAmount` and `IERC20(mWom).balanceOf(address(this))` end identical in both runs.

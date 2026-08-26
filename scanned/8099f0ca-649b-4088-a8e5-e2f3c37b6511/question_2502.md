# Q2502: SmartWomConvert.convert - shared mWOM balance is settled to whoever calls next

## Question
Consider wombat/SmartWomConvert.sol, where mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Assuming the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, can an unprivileged attacker turn this into a divergence between `amountRec from swapExactTokensForTokens` and `convertAmount minted 1:1 by IMWom(mWom).deposit` via `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, breaking the invariant that one caller must never be settled out of value another caller left behind and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: shared mWOM balance is settled to whoever calls next)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: mode 1 and mode 2 approve and forward obtainedmWomAmount out of the contract's own balance rather than out of a per-user ledger, so mWOM stranded in this contract by an earlier rounding or partial fill is handed to the next caller. Precondition: the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn.
- Invariant to test: one caller must never be settled out of value another caller left behind; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, then assert `amountRec from swapExactTokensForTokens` and `convertAmount minted 1:1 by IMWom(mWom).deposit` end identical in both runs.

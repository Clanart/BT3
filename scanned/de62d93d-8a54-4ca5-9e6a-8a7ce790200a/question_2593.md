# Q2593: SmartWomConvert.convert - safeApprove without reset on the mWOM mint leg

## Question
wombat/SmartWomConvert.sol: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. With _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR under attacker control and the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, can an unprivileged caller sequence `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` so that `currentRatio()` and `buybackThreshold` no longer reconcile, violating the invariant that the mint leg must remain usable regardless of allowance residue and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: safeApprove without reset on the mWOM mint leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Precondition: the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn.
- Invariant to test: the mint leg must remain usable regardless of allowance residue; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up the call arrives from WombatStaking._sendRewards with _mode zero and _minRec equal to _amountIn, snapshot `currentRatio()` and `buybackThreshold`, run the attacker's `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

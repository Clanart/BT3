# Q1125: SmartWomConvert.convert - safeApprove without reset on the mWOM mint leg

## Question
In wombat/SmartWomConvert.sol, _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Does `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` let an unprivileged caller exploit that under the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, so that `obtainedmWomAmount` diverges from `IERC20(mWom).balanceOf(address(this))`, the invariant that the mint leg must remain usable regardless of allowance residue is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: safeApprove without reset on the mWOM mint leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Precondition: the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs.
- Invariant to test: the mint leg must remain usable regardless of allowance residue; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, snapshot `obtainedmWomAmount` and `IERC20(mWom).balanceOf(address(this))`, run the attacker's `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

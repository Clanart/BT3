# Q1881: SmartWomConvert.convert - safeApprove without reset on the router leg

## Question
In wombat/SmartWomConvert.sol, _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Can an unprivileged attacker reach this through `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` while womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR, and drive `obtainedmWomAmount` out of agreement with `IERC20(mWom).balanceOf(address(this))` - breaking the invariant that an approval on a repeated path must be idempotent - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: safeApprove without reset on the router leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(router, buybackAmount) with no prior zeroing, so router allowance residue permanently disables the buyback leg and, through _sendRewards, the harvest path. Precondition: womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR, snapshot `obtainedmWomAmount` and `IERC20(mWom).balanceOf(address(this))`, run the attacker's `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

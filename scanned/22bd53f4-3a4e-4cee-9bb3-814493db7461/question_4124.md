# Q4124: SmartWomConvert.convert - safeApprove without reset on the mWOM mint leg

## Question
wombat/SmartWomConvert.sol - _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Can an unprivileged attacker controlling _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR, under a residual mWOM balance from an earlier rounding sits in the contract, exploit this through `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` to break the reconciliation between `_minRec` and `convertAmount + amountRec` and the invariant that the mint leg must remain usable regardless of allowance residue, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: safeApprove without reset on the mWOM mint leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() calls IERC20(wom).safeApprove(mWom, convertAmount) with no reset before IMWom(mWom).deposit, so residue there disables conversion entirely. Precondition: a residual mWOM balance from an earlier rounding sits in the contract.
- Invariant to test: the mint leg must remain usable regardless of allowance residue; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange a residual mWOM balance from an earlier rounding sits in the contract, call `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, and assert `_minRec` equals `convertAmount + amountRec` and that no account can withdraw more than it put in.

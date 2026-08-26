# Q4049: SmartWomConvert.convert - obtained amount computed from arithmetic rather than from balance delta

## Question
wombat/SmartWomConvert.sol - _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Can an unprivileged attacker controlling _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR, under a residual mWOM balance from an earlier rounding sits in the contract, exploit this through `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` to break the reconciliation between `obtainedmWomAmount` and `IERC20(mWom).balanceOf(address(this))` and the invariant that the amount credited to a user must equal the balance the contract actually received for them, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: obtained amount computed from arithmetic rather than from balance delta)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Precondition: a residual mWOM balance from an earlier rounding sits in the contract.
- Invariant to test: the amount credited to a user must equal the balance the contract actually received for them; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish a residual mWOM balance from an earlier rounding sits in the contract, have the attacker run `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`, then assert the victim's claimable value and the `obtainedmWomAmount` versus `IERC20(mWom).balanceOf(address(this))` relation are unchanged by the attacker's transaction.

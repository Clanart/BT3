# Q4169: SmartWomConvert.convertFor - obtained amount computed from arithmetic rather than from balance delta

## Question
In wombat/SmartWomConvert.sol, _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Does `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` let an unprivileged caller exploit that under a residual mWOM balance from an earlier rounding sits in the contract, so that `_convertRatio` diverges from `DENOMINATOR`, the invariant that the amount credited to a user must equal the balance the contract actually received for them is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: obtained amount computed from arithmetic rather than from balance delta)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Precondition: a residual mWOM balance from an earlier rounding sits in the contract.
- Invariant to test: the amount credited to a user must equal the balance the contract actually received for them; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound) under a residual mWOM balance from an earlier rounding sits in the contract, asserting on every row that the amount credited to a user must equal the balance the contract actually received for them.

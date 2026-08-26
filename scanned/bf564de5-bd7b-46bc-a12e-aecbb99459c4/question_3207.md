# Q3207: SmartWomConvert.convertFor - obtained amount computed from arithmetic rather than from balance delta

## Question
In wombat/SmartWomConvert.sol, _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Starting from a state where the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, can an unprivileged EOA use `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` to leave `_minRec` inconsistent with `convertAmount + amountRec`, violating the invariant that the amount credited to a user must equal the balance the contract actually received for them and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: obtained amount computed from arithmetic rather than from balance delta)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Precondition: the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two.
- Invariant to test: the amount credited to a user must equal the balance the contract actually received for them; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, call `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`, and assert `_minRec` equals `convertAmount + amountRec` and that no account can withdraw more than it put in.

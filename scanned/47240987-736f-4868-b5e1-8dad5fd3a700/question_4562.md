# Q4562: SmartWomConvert.convertFor - obtained amount computed from arithmetic rather than from balance delta

## Question
Note that in wombat/SmartWomConvert.sol, _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Can an attacker holding only tokens bought on market reach it via `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` under the attacker sandwiches the transaction on the wom/mWom Wombat pool and force `currentRatio()` apart from `buybackThreshold`, breaking the invariant that the amount credited to a user must equal the balance the contract actually received for them for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: obtained amount computed from arithmetic rather than from balance delta)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Precondition: the attacker sandwiches the transaction on the wom/mWom Wombat pool.
- Invariant to test: the amount credited to a user must equal the balance the contract actually received for them; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker sandwiches the transaction on the wom/mWom Wombat pool, call `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`, and assert `currentRatio()` equals `buybackThreshold` and that no account can withdraw more than it put in.

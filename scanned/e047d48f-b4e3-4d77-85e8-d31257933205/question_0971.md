# Q0971: SmartWomConvert.convert - obtained amount computed from arithmetic rather than from balance delta

## Question
Note that in wombat/SmartWomConvert.sol, _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Can an attacker holding only tokens bought on market reach it via `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` under the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs and force `_convertRatio` apart from `DENOMINATOR`, breaking the invariant that the amount credited to a user must equal the balance the contract actually received for them for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)` (mechanism: obtained amount computed from arithmetic rather than from balance delta)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR
- Exploit idea: _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Precondition: the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs.
- Invariant to test: the amount credited to a user must equal the balance the contract actually received for them; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode)`: constrain the setup so that the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, fuzz the attacker inputs (_convertRatio, _minRec and _mode, all unvalidated beyond _convertRatio <= DENOMINATOR), and assert after every call that the amount credited to a user must equal the balance the contract actually received for them.

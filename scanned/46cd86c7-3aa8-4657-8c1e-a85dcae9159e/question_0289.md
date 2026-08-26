# Q0289: SmartWomConvert.convertFor - obtained amount computed from arithmetic rather than from balance delta

## Question
In wombat/SmartWomConvert.sol, _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Does `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` let an unprivileged caller exploit that under the attacker has pushed mWom below buybackThreshold against wom in the same transaction, so that `_convertRatio` diverges from `DENOMINATOR`, the invariant that the amount credited to a user must equal the balance the contract actually received for them is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: obtained amount computed from arithmetic rather than from balance delta)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Precondition: the attacker has pushed mWom below buybackThreshold against wom in the same transaction.
- Invariant to test: the amount credited to a user must equal the balance the contract actually received for them; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker has pushed mWom below buybackThreshold against wom in the same transaction, have the attacker run `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`, then assert the victim's claimable value and the `_convertRatio` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.

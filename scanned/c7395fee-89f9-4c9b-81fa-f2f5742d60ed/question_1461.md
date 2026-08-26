# Q1461: SmartWomConvert.smartConvert - smartConvert is on the protocol harvest path

## Question
In wombat/SmartWomConvert.sol, WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. Can an unprivileged attacker reach this through `smartConvert(uint256 _amountIn, uint256 _mode)` while the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, and drive `_convertRatio` out of agreement with `DENOMINATOR` - breaking the invariant that protocol fee conversion must not be exposed to a price the harvest caller can set - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert is on the protocol harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. Precondition: the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs.
- Invariant to test: protocol fee conversion must not be exposed to a price the harvest caller can set; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, snapshot `_convertRatio` and `DENOMINATOR`, run the attacker's `smartConvert(uint256 _amountIn, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.

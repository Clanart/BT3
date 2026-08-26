# Q2180: SmartWomConvert.smartConvert - smartConvert is on the protocol harvest path

## Question
wombat/SmartWomConvert.sol: WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. Under womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR, is there an unprivileged sequence of `smartConvert(uint256 _amountIn, uint256 _mode)` that leaves `currentRatio()` unreconciled with `buybackThreshold`, violates the invariant that protocol fee conversion must not be exposed to a price the harvest caller can set, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert is on the protocol harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. Precondition: womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR.
- Invariant to test: protocol fee conversion must not be exposed to a price the harvest caller can set; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from) under womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR, asserting on every row that protocol fee conversion must not be exposed to a price the harvest caller can set.

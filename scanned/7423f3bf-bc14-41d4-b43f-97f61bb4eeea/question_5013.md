# Q5013: SmartWomConvert.smartConvert - smartConvert is on the protocol harvest path

## Question
In wombat/SmartWomConvert.sol, WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. Can an unprivileged attacker reach this through `smartConvert(uint256 _amountIn, uint256 _mode)` while the router leaves a non-zero allowance after the swap, and drive `currentRatio()` out of agreement with `buybackThreshold` - breaking the invariant that protocol fee conversion must not be exposed to a price the harvest caller can set - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert is on the protocol harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: protocol fee conversion must not be exposed to a price the harvest caller can set; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the router leaves a non-zero allowance after the swap, then assert `currentRatio()` and `buybackThreshold` end identical in both runs.

# Q4670: SmartWomConvert.smartConvert - smartConvert is on the protocol harvest path

## Question
wombat/SmartWomConvert.sol: WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. With _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from under attacker control and the attacker sandwiches the transaction on the wom/mWom Wombat pool, can an unprivileged caller sequence `smartConvert(uint256 _amountIn, uint256 _mode)` so that `_convertRatio` and `DENOMINATOR` no longer reconcile, violating the invariant that protocol fee conversion must not be exposed to a price the harvest caller can set and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert is on the protocol harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. Precondition: the attacker sandwiches the transaction on the wom/mWom Wombat pool.
- Invariant to test: protocol fee conversion must not be exposed to a price the harvest caller can set; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `smartConvert(uint256 _amountIn, uint256 _mode)`: constrain the setup so that the attacker sandwiches the transaction on the wom/mWom Wombat pool, fuzz the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from), and assert after every call that protocol fee conversion must not be exposed to a price the harvest caller can set.
